-- Verification run log (P5-1, FR-19/FR-20).
--
-- One row per measured run: the derived measurement against caliper truth at the
-- agreed reference point (FR-10), the deviation and pass/fail vs ±1 mm (FR-09), and
-- full traceability — which engine and config produced it. This is the dataset the
-- design-control evidence export (P5-2) is built from.

create table if not exists public.runs (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),

    -- provenance
    job_id          uuid references public.jobs (id) on delete set null,
    model_name      text not null,          -- the physical stoma / test model
    video_ref       text,                   -- storage path of the source video

    -- measurement vs truth
    reference_point text,                    -- FR-10 caliper reference point (TBD w/ Cole)
    metric          text not null default 'diameter',
    truth_mm        numeric,                 -- caliper truth (null if not measured)
    measured_mm     numeric not null,
    deviation_mm    numeric,                 -- measured − truth
    abs_deviation_mm numeric,
    tolerance_mm    numeric not null default 1.0,
    passed          boolean,

    -- traceability (FR-20): reproduce this row from these
    engine          text,                    -- reconstruction engine
    method          text,                    -- measurement method (baseline / auto-height)
    config          jsonb not null default '{}'::jsonb,
    notes           text
);

comment on table public.runs is
    'One verification run: measurement vs caliper truth, deviation, pass/fail, engine+config.';

create index if not exists runs_model_idx on public.runs (model_name);
create index if not exists runs_created_idx on public.runs (created_at);
create index if not exists runs_passed_idx on public.runs (passed);

-- Service-role only, same posture as jobs (decisions.md D7). No PHI here.
alter table public.runs enable row level security;
