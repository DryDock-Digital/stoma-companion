// Engineer test bench at /admin. Never linked from the patient flow. Technical detail
// is the point here (the opposite of the patient screens). Never simulates.

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  API_BASE,
  createScan,
  describeError,
  getScan,
  gcodeUrl,
  hasBackend,
  listScans,
  patchScan,
  reportCsvUrl,
  type AdminScanDetail,
  type AdminScanSummary,
  type PatchRunInput,
} from "../api/admin";
import { OutlineChart } from "../components/OutlineChart";
import {
  fmtIso,
  fmtMm,
  fmtSec,
  fmtSigned,
  fmtWhen,
  KVTable,
  Notice,
  Panel,
  PassBadge,
  ProfileChart,
  shortId,
  StageTimeline,
  StatusBadge,
  type ProfilePoint,
} from "../components/admin/bits";
import { isTerminal, type ScanResult } from "../lib/flow";

// ---------------------------------------------------------------------------
// Hash routing within /admin: #/  #/new  #/run/<id>
// ---------------------------------------------------------------------------

type Route = { view: "list" } | { view: "new" } | { view: "run"; id: string };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (h === "new") return { view: "new" };
  const m = h.match(/^run\/(.+)$/);
  if (m) return { view: "run", id: decodeURIComponent(m[1]) };
  return { view: "list" };
}

export const href = {
  list: "#/",
  new: "#/new",
  run: (id: string) => `#/run/${encodeURIComponent(id)}`,
};

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

export function Admin() {
  const route = useRoute();
  return (
    <div className="mx-auto min-h-[100dvh] w-full max-w-7xl px-6 py-5">
      <header className="mb-6 flex items-center justify-between border-b border-line pb-4">
        <div className="flex items-baseline gap-4">
          <a href={href.list} className="text-lg font-semibold tracking-tight">
            Stoma Companion <span className="text-accent">/ test bench</span>
          </a>
          <nav className="flex gap-3 text-sm text-muted">
            <a className={route.view === "list" ? "text-ink" : "hover:text-ink"} href={href.list}>
              Runs
            </a>
            <a className={route.view === "new" ? "text-ink" : "hover:text-ink"} href={href.new}>
              New run
            </a>
            {hasBackend() && (
              <a className="hover:text-ink" href={reportCsvUrl()} target="_blank" rel="noreferrer">
                report.csv ↗
              </a>
            )}
          </nav>
        </div>
        <span className="font-mono text-xs text-faint">{hasBackend() ? API_BASE : "no backend"}</span>
      </header>

      {!hasBackend() ? (
        <Notice tone="warn">
          <strong>No backend configured.</strong> Set <code className="font-mono">VITE_API_BASE</code> at build time. The admin bench never
          simulates.
        </Notice>
      ) : route.view === "list" ? (
        <RunsList />
      ) : route.view === "new" ? (
        <NewRun />
      ) : (
        <RunDetail id={route.id} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Runs list
// ---------------------------------------------------------------------------

const LIST_REFRESH_MS = 10_000;

function RunsList() {
  const [jobs, setJobs] = useState<AdminScanSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const list = await listScans(50, signal);
      setJobs(list);
      setError(null);
      setRefreshedAt(Date.now());
    } catch (err) {
      if (signal?.aborted) return;
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  const anyRunning = (jobs ?? []).some((j) => !isTerminal(j.status));
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => void load(), LIST_REFRESH_MS);
    return () => clearInterval(t);
  }, [anyRunning, load]);

  const agg = useMemo(() => {
    const list = jobs ?? [];
    const measured = list.filter((j) => j.diameter_mm != null);
    const withTruth = measured.filter((j) => j.truth_mm != null && j.deviation_mm != null);
    const devs = withTruth.map((j) => Math.abs(j.deviation_mm as number));
    return {
      total: list.length,
      measured: measured.length,
      withTruth: withTruth.length,
      pass: withTruth.filter((j) => j.within_tolerance === true).length,
      meanAbs: devs.length ? devs.reduce((a, b) => a + b, 0) / devs.length : null,
      maxAbs: devs.length ? Math.max(...devs) : null,
    };
  }, [jobs]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="runs" value={String(agg.total)} />
        <Stat label="measured" value={String(agg.measured)} />
        <Stat label="with truth" value={String(agg.withTruth)} />
        <Stat
          label="pass"
          value={agg.withTruth ? `${agg.pass} / ${agg.withTruth}` : "—"}
          tone={agg.withTruth ? (agg.pass === agg.withTruth ? "ok" : "fail") : undefined}
        />
        <Stat label="mean |dev|" value={fmtMm(agg.meanAbs)} />
        <Stat label="max |dev|" value={fmtMm(agg.maxAbs)} />
      </div>

      {error && (
        <Notice tone="error">
          Could not load runs: {error}{" "}
          <button className="ml-2 underline" onClick={() => void load()}>
            retry
          </button>
        </Notice>
      )}

      <Panel
        title="Runs"
        right={
          <div className="flex items-center gap-3 text-xs text-faint">
            {anyRunning && <span className="text-cyan">auto-refresh 10 s</span>}
            {refreshedAt && <span>refreshed {fmtWhen(new Date(refreshedAt).toISOString())}</span>}
            <button className="rounded-md border border-line px-2 py-0.5 hover:text-ink" onClick={() => void load()}>
              refresh
            </button>
            <a className="rounded-md bg-accent px-2.5 py-0.5 font-semibold text-accent-ink" href={href.new}>
              + New run
            </a>
          </div>
        }
        className="overflow-x-auto"
      >
        {jobs === null && !error ? (
          <p className="text-sm text-faint">loading…</p>
        ) : jobs && jobs.length === 0 ? (
          <p className="text-sm text-faint">
            No runs yet.{" "}
            <a className="text-accent underline" href={href.new}>
              Start one.
            </a>
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-faint">
              <tr>
                <th className="pb-2 pr-3">id</th>
                <th className="pb-2 pr-3">model</th>
                <th className="pb-2 pr-3">status</th>
                <th className="pb-2 pr-3 text-right">Ø measured</th>
                <th className="pb-2 pr-3 text-right">truth</th>
                <th className="pb-2 pr-3 text-right">deviation</th>
                <th className="pb-2 pr-3">result</th>
                <th className="pb-2 pr-3 text-right">total</th>
                <th className="pb-2 pr-3">engine</th>
                <th className="pb-2">when</th>
              </tr>
            </thead>
            <tbody>
              {(jobs ?? []).map((j) => (
                <tr key={j.id} className="border-t border-line hover:bg-white/[0.03]">
                  <td className="py-2 pr-3 font-mono text-xs">
                    <a className="text-accent hover:underline" href={href.run(j.id)} title={j.id}>
                      {shortId(j.id)}
                    </a>
                  </td>
                  <td className="py-2 pr-3">{j.model_name ?? <span className="text-faint">—</span>}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={j.status} />
                    {j.attempts > 1 && <span className="ml-1 text-xs text-faint">×{j.attempts}</span>}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{fmtMm(j.diameter_mm)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{fmtMm(j.truth_mm)}</td>
                  <td className={`py-2 pr-3 text-right font-mono ${devTone(j.deviation_mm, j.tolerance_mm)}`}>{fmtSigned(j.deviation_mm)}</td>
                  <td className="py-2 pr-3">
                    <PassBadge pass={j.within_tolerance} tolerance={j.tolerance_mm} />
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{fmtSec(j.total_s)}</td>
                  <td className="py-2 pr-3 text-xs text-muted">{j.engine ?? "—"}</td>
                  <td className="py-2 text-xs text-muted" title={fmtIso(j.created_at)}>
                    {fmtWhen(j.created_at)}
                    {j.error && <div className="max-w-[16rem] truncate text-danger" title={j.error}>{j.error}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}

function devTone(dev: number | null, tol: number): string {
  if (dev == null) return "text-faint";
  return Math.abs(dev) <= tol ? "text-success" : "text-danger";
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "ok" | "fail" }) {
  const color = tone === "ok" ? "text-success" : tone === "fail" ? "text-danger" : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-surface/60 px-4 py-3">
      <div className="text-xs uppercase tracking-wider text-faint">{label}</div>
      <div className={`mt-1 font-mono text-xl ${color}`}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. New run
// ---------------------------------------------------------------------------

const DEFAULT_REFERENCE = "base at skin junction";

function NewRun() {
  const [file, setFile] = useState<File | null>(null);
  const [model, setModel] = useState("");
  const [truth, setTruth] = useState("");
  const [reference, setReference] = useState(DEFAULT_REFERENCE);
  const [notes, setNotes] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busy = progress !== null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return setError("Pick a video file first.");
    const truthNum = truth.trim() === "" ? null : Number(truth);
    if (truthNum !== null && !Number.isFinite(truthNum)) return setError("Caliper truth must be a number (mm).");
    setError(null);
    setProgress(0);
    try {
      const created = await createScan(
        { video: file, model_name: model.trim() || undefined, truth_mm: truthNum, reference_point: reference.trim() || undefined, notes: notes.trim() || undefined },
        setProgress,
      );
      window.location.hash = href.run(created.id);
    } catch (err) {
      setError(describeError(err));
      setProgress(null);
    }
  };

  return (
    <form onSubmit={submit} className="mx-auto max-w-xl">
      <Panel title="New run">
        <div className="space-y-4">
          <Field label="Video (any phone, plain camera — NFR-04)">
            <input
              type="file"
              accept="video/*"
              disabled={busy}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-line-strong file:bg-white/[0.04] file:px-3 file:py-1.5 file:text-sm file:text-ink"
            />
            {file && (
              <div className="mt-1 font-mono text-xs text-faint">
                {file.name} · {(file.size / 1e6).toFixed(1)} MB · {file.type || "unknown type"}
              </div>
            )}
          </Field>
          <Field label="Model name">
            <Input value={model} onChange={setModel} placeholder="e.g. phantom-A 30mm" disabled={busy} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Caliper truth (mm, optional)">
              <Input value={truth} onChange={setTruth} placeholder="30.00" type="number" step="0.01" disabled={busy} />
            </Field>
            <Field label="Reference point">
              <Input value={reference} onChange={setReference} disabled={busy} />
            </Field>
          </div>
          <Field label="Notes">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              disabled={busy}
              className="w-full rounded-md border border-line bg-base/60 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            />
          </Field>

          {error && <Notice tone="error">{error}</Notice>}

          {busy && (
            <div>
              <div className="mb-1 flex justify-between text-xs text-muted">
                <span>{progress !== null && progress < 1 ? "uploading…" : "waiting for server…"}</span>
                <span className="font-mono">{Math.round((progress ?? 0) * 100)}%</span>
              </div>
              <div className="h-1.5 w-full rounded bg-white/[0.06]">
                <div className="h-full rounded bg-accent transition-[width]" style={{ width: `${(progress ?? 0) * 100}%` }} />
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-3 pt-2">
            <a href={href.list} className="text-sm text-muted hover:text-ink">
              cancel
            </a>
            <button type="submit" disabled={busy || !file} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:opacity-40">
              Upload &amp; run
            </button>
          </div>
        </div>
      </Panel>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wider text-faint">{label}</span>
      {children}
    </label>
  );
}

function Input({ value, onChange, placeholder, type = "text", step, disabled }: { value: string; onChange: (v: string) => void; placeholder?: string; type?: string; step?: string; disabled?: boolean }) {
  return (
    <input
      type={type}
      step={step}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-line bg-base/60 px-3 py-2 text-sm text-ink outline-none focus:border-accent disabled:opacity-50"
    />
  );
}

// ---------------------------------------------------------------------------
// 3. Run detail
// ---------------------------------------------------------------------------

const DETAIL_POLL_MS = 5_000;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

function pointList(v: unknown): [number, number][] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out: [number, number][] = [];
  for (const p of v) {
    if (Array.isArray(p) && p.length >= 2 && typeof p[0] === "number" && typeof p[1] === "number") out.push([p[0], p[1]]);
  }
  return out.length ? out : undefined;
}

function profileList(v: unknown): ProfilePoint[] {
  if (!Array.isArray(v)) return [];
  const out: ProfilePoint[] = [];
  for (const p of v) {
    if (Array.isArray(p) && p.length >= 2 && typeof p[0] === "number") out.push([p[0], typeof p[1] === "number" && Number.isFinite(p[1]) ? p[1] : null]);
  }
  return out;
}

function RunDetail({ id }: { id: string }) {
  const [job, setJob] = useState<AdminScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const stale = useRef(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const d = await getScan(id, signal);
        setJob(d);
        setError(null);
        setNotFound(false);
      } catch (err) {
        if (signal?.aborted) return;
        if (err instanceof Error && "kind" in err && (err as { kind?: string }).kind === "not_found") setNotFound(true);
        setError(describeError(err));
      }
    },
    [id],
  );

  useEffect(() => {
    stale.current = false;
    const ac = new AbortController();
    setJob(null);
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  const running = job ? !isTerminal(job.status) : !notFound;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), DETAIL_POLL_MS);
    return () => clearInterval(t);
  }, [running, load]);

  if (notFound)
    return (
      <Notice tone="error">
        Run <code className="font-mono">{id}</code> not found (404).{" "}
        <a className="underline" href={href.list}>
          Back to runs
        </a>
      </Notice>
    );
  if (!job)
    return error ? (
      <Notice tone="error">
        {error}{" "}
        <button className="ml-2 underline" onClick={() => void load()}>
          retry
        </button>
      </Notice>
    ) : (
      <p className="text-sm text-faint">loading {id}…</p>
    );

  const result = job.result;
  const diagnostics = obj(result?.diagnostics) ?? {};
  const clearance = obj(result?.clearance_mm);
  const reconstruction = obj(result?.reconstruction);
  const sliceHeight = num(diagnostics.slice_height_mm_above_skin);
  const profile = profileList(diagnostics.diameter_profile);
  const outline: ScanResult | null = result
    ? { outline_mm: pointList(result.outline_mm), wafer_outline_mm: pointList(result.wafer_outline_mm) }
    : null;
  const diameter = job.diameter_mm ?? num(result?.diameter_mm);
  const a = job.artifacts;

  return (
    <div className="space-y-4">
      {error && <Notice tone="warn">Last refresh failed: {error} (showing previous data)</Notice>}

      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-lg">{job.id}</h1>
            <StatusBadge status={job.status} />
            {running && <span className="text-xs text-cyan">polling 5 s</span>}
          </div>
          <div className="mt-1 text-sm text-muted">
            {job.model_name ?? <span className="text-faint">unnamed model</span>} · created {fmtIso(job.created_at)} · updated {fmtWhen(job.updated_at)}
            {job.engine && <> · engine <span className="font-mono">{job.engine}</span></>}
            {job.worker_id && <> · worker <span className="font-mono">{job.worker_id}</span></>}
            {job.attempts > 1 && <> · attempt {job.attempts}</>}
            {job.keyframe_count != null && <> · {job.keyframe_count} keyframes</>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <DlLink href={a.video_url} label="video" />
          <DlLink href={a.mesh_url} label="mesh (OBJ)" />
          <DlLink href={a.poses_url} label="poses" />
          <DlLink href={a.gcode_url ?? (result ? gcodeUrl(job.id) : null)} label="G-code" />
        </div>
      </div>

      {job.status === "failed" && (
        <Panel title={`Failed${job.error_stage ? ` at ${job.error_stage}` : ""}`}>
          <p className="text-sm text-danger">{job.error ?? "no error message"}</p>
          {job.error_detail && <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-base/70 p-3 font-mono text-xs text-muted">{job.error_detail}</pre>}
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* measurement */}
        <Panel title="Measurement">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-5xl">{diameter != null ? diameter.toFixed(2) : "—"}</span>
            <span className="text-muted">mm Ø</span>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-faint">caliper truth</dt>
            <dd className="text-right font-mono">{fmtMm(job.truth_mm)}</dd>
            <dt className="text-faint">deviation</dt>
            <dd className={`text-right font-mono ${devTone(job.deviation_mm, job.tolerance_mm)}`}>{fmtSigned(job.deviation_mm)}</dd>
            <dt className="text-faint">tolerance</dt>
            <dd className="text-right font-mono">±{job.tolerance_mm} mm</dd>
            <dt className="text-faint">result</dt>
            <dd className="text-right">
              <PassBadge pass={job.within_tolerance} tolerance={job.tolerance_mm} />
            </dd>
            {result && (
              <>
                <dt className="text-faint">grace ring</dt>
                <dd className="text-right font-mono">{fmtMm(num(result.grace_ring_mm), 1)}</dd>
                <dt className="text-faint">scale</dt>
                <dd className="text-right font-mono">{num(result.scale_mm_per_unit)?.toFixed(4) ?? "—"} mm/unit</dd>
                <dt className="text-faint">marker views</dt>
                <dd className="text-right font-mono">{fmtValue2(result.marker_views)}</dd>
                <dt className="text-faint">registered frames</dt>
                <dd className="text-right font-mono">{fmtValue2(result.registered_frames)}</dd>
                <dt className="text-faint">orientation</dt>
                <dd className="text-right font-mono text-xs">{str(result.orientation_method) ?? "—"}</dd>
                <dt className="text-faint">G-code dialect</dt>
                <dd className="text-right font-mono text-xs">{str(result.gcode_dialect) ?? "—"}</dd>
                {clearance && (
                  <>
                    <dt className="text-faint">clearance min/mean/max</dt>
                    <dd className="text-right font-mono text-xs">
                      {num(clearance.min)?.toFixed(2)} / {num(clearance.mean)?.toFixed(2)} / {num(clearance.max)?.toFixed(2)} mm{" "}
                      {clearance.passes === true ? <span className="text-success">ok</span> : clearance.passes === false ? <span className="text-danger">fail</span> : null}
                    </dd>
                  </>
                )}
              </>
            )}
          </dl>
          <p className="mt-3 text-xs text-faint">
            reference: {job.reference_point ?? "—"}
          </p>
        </Panel>

        {/* timeline */}
        <Panel title="Stage timeline">
          <StageTimeline timings={job.timings} />
        </Panel>

        {/* caliper form */}
        <CaliperForm job={job} onSaved={setJob} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Outline (base + wafer cut line)">
          {outline?.outline_mm || outline?.wafer_outline_mm ? (
            <div className="mx-auto aspect-square max-w-md">
              <OutlineChart result={outline} />
            </div>
          ) : (
            <p className="text-sm text-faint">no outline yet</p>
          )}
        </Panel>
        <Panel title="Ø vs height above skin">
          <ProfileChart profile={profile} sliceHeight={sliceHeight} baseDiameter={diameter} />
          {num(diagnostics.polar_diameter_at_base_mm) != null && (
            <p className="mt-2 text-xs text-faint">polar Ø at base: {fmtMm(num(diagnostics.polar_diameter_at_base_mm))} · method: {str(diagnostics.outline_method) ?? "—"}</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Diagnostics">
          <KVTable data={omit(diagnostics, ["diameter_profile"])} />
        </Panel>
        <Panel title="Config">
          <KVTable data={job.config} />
        </Panel>
        <Panel title="Reconstruction / run log">
          {reconstruction && <KVTable data={reconstruction} />}
          {job.run && (
            <div className={reconstruction ? "mt-4 border-t border-line pt-3" : ""}>
              <div className="mb-1 text-xs uppercase tracking-wider text-faint">verification log row</div>
              <KVTable data={job.run} />
            </div>
          )}
          {!reconstruction && !job.run && <p className="text-sm text-faint">nothing yet</p>}
          {result?.upload_bytes != null && <p className="mt-2 text-xs text-faint">upload {((num(result.upload_bytes) ?? 0) / 1e6).toFixed(1)} MB</p>}
        </Panel>
      </div>

      {a.keyframe_urls.length > 0 && (
        <Panel title={`Keyframes (${a.keyframe_urls.length})`}>
          <div className="grid grid-cols-4 gap-2 md:grid-cols-8 lg:grid-cols-12">
            {a.keyframe_urls.map((u, i) => (
              <a key={u} href={u} target="_blank" rel="noreferrer" className="overflow-hidden rounded-md border border-line">
                <img src={u} alt={`keyframe ${i}`} loading="lazy" className="aspect-square w-full object-cover" />
              </a>
            ))}
          </div>
        </Panel>
      )}

      {job.notes && (
        <Panel title="Notes">
          <p className="whitespace-pre-wrap text-sm">{job.notes}</p>
        </Panel>
      )}

      <details className="rounded-2xl border border-line bg-surface/60">
        <summary className="cursor-pointer px-4 py-2.5 text-sm font-semibold uppercase tracking-wider text-muted">Raw JSON</summary>
        <pre className="max-h-[32rem] overflow-auto border-t border-line p-4 font-mono text-xs text-muted">{JSON.stringify(job, null, 2)}</pre>
      </details>
    </div>
  );
}

const fmtValue2 = (v: unknown) => (v == null ? "—" : typeof v === "number" ? String(v) : JSON.stringify(v));

function omit(o: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(o)) if (!keys.includes(k)) out[k] = o[k];
  return out;
}

function DlLink({ href: url, label }: { href: string | null; label: string }) {
  if (!url) return <span className="rounded-md border border-line px-2.5 py-1 text-faint">{label}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="rounded-md border border-line-strong bg-white/[0.04] px-2.5 py-1 text-ink hover:bg-white/[0.08]">
      {label} ↓
    </a>
  );
}

function CaliperForm({ job, onSaved }: { job: AdminScanDetail; onSaved: (d: AdminScanDetail) => void }) {
  const [model, setModel] = useState(job.model_name ?? "");
  const [truth, setTruth] = useState(job.truth_mm != null ? String(job.truth_mm) : "");
  const [reference, setReference] = useState(job.reference_point ?? DEFAULT_REFERENCE);
  const [notes, setNotes] = useState(job.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ tone: "info" | "error"; text: string } | null>(null);

  // Re-seed when navigating to a different run.
  useEffect(() => {
    setModel(job.model_name ?? "");
    setTruth(job.truth_mm != null ? String(job.truth_mm) : "");
    setReference(job.reference_point ?? DEFAULT_REFERENCE);
    setNotes(job.notes ?? "");
    setMsg(null);
  }, [job.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const truthNum = truth.trim() === "" ? null : Number(truth);
    if (truthNum !== null && !Number.isFinite(truthNum)) return setMsg({ tone: "error", text: "Truth must be a number (mm)." });
    const patch: PatchRunInput = { model_name: model.trim(), truth_mm: truthNum, reference_point: reference.trim(), notes: notes.trim() };
    setSaving(true);
    setMsg(null);
    try {
      const updated = await patchScan(job.id, patch);
      onSaved(updated);
      setMsg({ tone: "info", text: `Saved · deviation ${fmtSigned(updated.deviation_mm)}` });
    } catch (err) {
      setMsg({ tone: "error", text: describeError(err) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel title="Caliper truth">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Model name">
          <Input value={model} onChange={setModel} disabled={saving} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Truth (mm)">
            <Input value={truth} onChange={setTruth} type="number" step="0.01" placeholder="blank = none" disabled={saving} />
          </Field>
          <Field label="Reference point">
            <Input value={reference} onChange={setReference} disabled={saving} />
          </Field>
        </div>
        <Field label="Notes">
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            disabled={saving}
            className="w-full rounded-md border border-line bg-base/60 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
        </Field>
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
        <div className="flex justify-end">
          <button type="submit" disabled={saving} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:opacity-40">
            {saving ? "Saving…" : "Save & recompute"}
          </button>
        </div>
      </form>
    </Panel>
  );
}
