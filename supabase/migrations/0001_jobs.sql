-- Stoma Companion — job state + storage skeleton (P0-7)
--
-- One row per scan. The reconstruction engine is deliberately NOT modelled here
-- as anything more than a free-text `engine` label written by whichever worker
-- claimed the job — nothing in the schema may depend on which engine ran
-- (CLAUDE.md architecture contract, decisions.md D3).
--
-- State machine (see docs/queue-contract.md):
--   pending → extracting → keyframes_ready → reconstructing → mesh_ready
--             → measuring → measured → cutting → done
--   any state → failed
--
-- Apply with the Supabase CLI:  supabase db push
-- or paste into the SQL editor of a fresh project.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- jobs
-- ---------------------------------------------------------------------------
create table if not exists public.jobs (
    id              uuid primary key default gen_random_uuid(),
    status          text not null default 'pending',

    -- storage paths inside the `scans` bucket (see below). Kept as plain text so
    -- the DB has no coupling to the storage layout beyond these pointers.
    video_path      text,               -- scans/<id>/input.mov
    keyframes_prefix text,              -- scans/<id>/keyframes/
    keyframe_count  integer,
    mesh_path       text,               -- scans/<id>/mesh.obj

    -- reconstruction worker bookkeeping (P1-3 contract)
    engine          text,               -- 'colmap+openmvs', 'apple-photogrammetry', …
    worker_id       text,               -- opaque id of the worker holding the claim
    claimed_at      timestamptz,

    -- knobs carried with the job so a run is reproducible: keyframe interval,
    -- frame cap, grace-ring mm, slice params, … (FR-07 stays configurable).
    config          jsonb not null default '{}'::jsonb,

    -- downstream results (measurement, outline, g-code refs) land here later.
    result          jsonb,

    error           text,

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

comment on table public.jobs is
    'One scan per row. Engine-agnostic: `engine` is a label, never a dependency.';

-- Allowed states — a CHECK keeps typo statuses from wedging the queue.
alter table public.jobs
    drop constraint if exists jobs_status_check;
alter table public.jobs
    add constraint jobs_status_check check (status in (
        'pending',
        'extracting',
        'keyframes_ready',
        'reconstructing',
        'mesh_ready',
        'measuring',
        'measured',
        'cutting',
        'done',
        'failed'
    ));

-- Workers poll for the oldest claimable job; index the hot path.
create index if not exists jobs_status_created_idx
    on public.jobs (status, created_at);

-- keep updated_at honest
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists jobs_touch_updated_at on public.jobs;
create trigger jobs_touch_updated_at
    before update on public.jobs
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- atomic claim — a worker grabs the next reconstructable job without racing
-- other workers. Flips keyframes_ready → reconstructing under a row lock.
-- ---------------------------------------------------------------------------
create or replace function public.claim_next_job(p_worker_id text, p_engine text)
returns setof public.jobs
language plpgsql as $$
declare
    v_id uuid;
begin
    select id into v_id
    from public.jobs
    where status = 'keyframes_ready'
    order by created_at
    for update skip locked
    limit 1;

    if v_id is null then
        return;
    end if;

    return query
    update public.jobs
    set status     = 'reconstructing',
        worker_id  = p_worker_id,
        engine     = p_engine,
        claimed_at = now()
    where id = v_id
    returning *;
end;
$$;

-- ---------------------------------------------------------------------------
-- Storage bucket — private. The backend signs URLs; clients never touch it
-- directly. HIPAA/PHI is deferred (NFR-07) but keeping the bucket private now
-- means the compliance phase is an upgrade, not a rebuild (NFR-02/NFR-03).
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('scans', 'scans', false)
on conflict (id) do nothing;

-- No RLS policies are added in this phase: the backend uses the service-role
-- key and is the only writer. Auth/RLS arrives with the compliance phase.
