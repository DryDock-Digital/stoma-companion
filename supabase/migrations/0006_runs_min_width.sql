-- Non-circular stomas: log the narrowest caliper span alongside the widest, with its
-- own truth and deviation; `passed` means every provided reading is within tolerance.
alter table public.runs
    add column if not exists measured_min_mm  double precision,
    add column if not exists truth_min_mm     double precision,
    add column if not exists deviation_min_mm double precision;
