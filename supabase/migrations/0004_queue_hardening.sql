-- Stoma Companion — queue hardening (review fixes, 2026-08-26)
--
--  * generic claim: claim_next_job(worker, engine, from_status, to_status) so the
--    keyframe, reconstruction, measurement and (P4) cutting stages all use one
--    atomic FOR UPDATE SKIP LOCKED path
--  * attempts + requeue_stale_jobs(): a worker that dies mid-stage no longer strands
--    the row; the watchdog returns it to the claimable state, then fails it for good
--  * patient-safe `error` vs raw `error_detail` + `error_stage`
--  * poses_path / gcode_path artefact pointers (engine-neutral poses; G-code is an
--    object, never a JSON blob in `result`)
--  * patch_job_result(): server-side merge so stages in different processes never
--    clobber each other's `result` (timings merge one level deep)
--  * queue_stats(): counts by status + oldest claim age for /health

alter table public.jobs
    add column if not exists poses_path   text,
    add column if not exists gcode_path   text,
    add column if not exists attempts     integer not null default 0,
    add column if not exists error_detail text,
    add column if not exists error_stage  text;

comment on column public.jobs.error is
    'Patient-safe sentence shown by the app as-is. Raw text lives in error_detail.';

-- generic atomic claim ------------------------------------------------------
drop function if exists public.claim_next_job(text, text);
create or replace function public.claim_next_job(
    p_worker_id text,
    p_engine    text,
    p_from      text default 'keyframes_ready',
    p_to        text default 'reconstructing'
)
returns setof public.jobs
language plpgsql as $$
declare
    v_id uuid;
begin
    select id into v_id
    from public.jobs
    where status = p_from
    order by created_at
    for update skip locked
    limit 1;

    if v_id is null then
        return;
    end if;

    return query
    update public.jobs
    set status     = p_to,
        worker_id  = p_worker_id,
        engine     = coalesce(p_engine, engine),
        claimed_at = now(),
        attempts   = attempts + 1
    where id = v_id
    returning *;
end;
$$;

-- stale-claim watchdog ------------------------------------------------------
create or replace function public.requeue_stale_jobs(
    p_older_than_s double precision,
    p_max_attempts integer
)
returns setof public.jobs
language plpgsql as $$
begin
    return query
    update public.jobs j
    set status = case
            when j.attempts >= p_max_attempts then 'failed'
            when j.status = 'extracting'      then 'pending'
            when j.status = 'reconstructing'  then 'keyframes_ready'
            when j.status = 'measuring'       then 'mesh_ready'
            when j.status = 'cutting'         then 'measured'
        end,
        worker_id  = case when j.attempts >= p_max_attempts then j.worker_id else null end,
        claimed_at = case when j.attempts >= p_max_attempts then j.claimed_at else null end,
        error = case when j.attempts >= p_max_attempts
            then 'This took longer than expected. Please try again.' else j.error end,
        error_detail = case when j.attempts >= p_max_attempts
            then format('stage %s claimed by %s at %s never completed (%s attempts)',
                        j.status, j.worker_id, j.claimed_at, j.attempts)
            else j.error_detail end,
        error_stage = case when j.attempts >= p_max_attempts then 'timeout' else j.error_stage end
    where j.status in ('extracting', 'reconstructing', 'measuring', 'cutting')
      and j.claimed_at is not null
      and j.claimed_at < now() - make_interval(secs => p_older_than_s)
    returning j.*;
end;
$$;

-- server-side result merge --------------------------------------------------
create or replace function public.patch_job_result(p_id uuid, p_patch jsonb)
returns setof public.jobs
language plpgsql as $$
begin
    return query
    update public.jobs
    set result = coalesce(result, '{}'::jsonb)
                 || (p_patch - 'timings_s')
                 || case when p_patch ? 'timings_s'
                        then jsonb_build_object('timings_s',
                                coalesce(result->'timings_s', '{}'::jsonb) || (p_patch->'timings_s'))
                        else '{}'::jsonb end
    where id = p_id
    returning *;
end;
$$;

-- /health -------------------------------------------------------------------
create or replace function public.queue_stats()
returns jsonb
language sql stable as $$
    select jsonb_build_object(
        'counts', coalesce((select jsonb_object_agg(status, n)
                            from (select status, count(*) n from public.jobs group by status) s),
                           '{}'::jsonb),
        'oldest_claim_age_s', (select extract(epoch from now() - min(claimed_at))
                               from public.jobs
                               where status in ('extracting','reconstructing','measuring','cutting')
                                 and claimed_at is not null)
    );
$$;
