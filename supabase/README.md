# supabase — job state + storage (P0-7)

Schema and storage for the demo backend. Supabase was chosen for the signed-BAA
HIPAA upgrade path so the compliance phase is an upgrade, not a rebuild
(decisions.md D4, NFR-03).

## What's here

- `migrations/0001_jobs.sql` — `public.jobs` table, `claim_next_job()` RPC, and the
  private `scans` storage bucket.
- `config.toml` — Supabase CLI config for a local stack.

## Provisioning a hosted project (needs Aaron's Supabase account)

This directory is infrastructure-as-code; it does **not** create a live project.
To stand one up:

```bash
# 1. Create the project in the Supabase dashboard (or `supabase projects create`),
#    then grab its ref (the subdomain of the project URL).
supabase link --project-ref <project-ref>

# 2. Push the schema + bucket.
supabase db push

# 3. Copy the URL + keys into backend/.env (see .env.example):
#    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY
```

## Local stack (no cloud, for tests/dev)

```bash
supabase start          # boots Postgres + storage in Docker
supabase db reset       # applies migrations/ into the local db
```

The backend's tests do **not** need any of this — they run against an in-memory
job store (`backend/app/store.py::InMemoryJobStore`). The live Supabase client is
only exercised in deployment.

## `jobs` state machine

See [docs/queue-contract.md](../docs/queue-contract.md) for the authoritative
description. Reconstruction workers claim jobs via `claim_next_job(worker_id, engine)`,
which atomically flips `keyframes_ready → reconstructing` under `FOR UPDATE SKIP
LOCKED` so multiple workers never grab the same job.
