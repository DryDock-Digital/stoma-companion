-- A `pending` job is claimable only once its video is stored: POST /scans inserts
-- the row before the (slow) upload to storage completes, and the keyframe worker
-- polls every second. Found on the first real upload (2026-08-26).
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
      and (p_from <> 'pending' or video_path is not null)
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
