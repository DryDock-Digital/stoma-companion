// Small presentational pieces for the admin test bench. Technical detail welcome here.

import type { ReactNode } from "react";
import type { AdminTimings, JobStatus } from "../../api/admin";
import { isTerminal } from "../../lib/flow";

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

export const fmtMm = (v: number | null | undefined, dp = 2) =>
  v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(dp)} mm`;

export const fmtSigned = (v: number | null | undefined, dp = 2) =>
  v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)} mm`;

export const fmtSec = (v: number | null | undefined, dp = 1) =>
  v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(dp)} s`;

export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.max(0, Math.round(diff))} s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return d.toLocaleString();
}

export const fmtIso = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

export function fmtValue(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    if (v.length > 12) return `[${v.length} items]`;
    return `[${v.map(fmtValue).join(", ")}]`;
  }
  return JSON.stringify(v);
}

export const shortId = (id: string) => (id.length > 12 ? `${id.slice(0, 8)}…` : id);

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

type Tone = "running" | "ok" | "fail" | "idle";

function statusTone(s: JobStatus): Tone {
  if (s === "failed") return "fail";
  if (s === "measured" || s === "done") return "ok";
  if (s === "pending") return "idle";
  return "running";
}

const TONE_CLASS: Record<Tone, string> = {
  running: "border-cyan/30 bg-cyan/10 text-cyan",
  ok: "border-success/30 bg-success/10 text-success",
  fail: "border-danger/30 bg-danger/10 text-danger",
  idle: "border-line-strong bg-white/[0.04] text-muted",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const tone = statusTone(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-xs ${TONE_CLASS[tone]}`}
    >
      {!isTerminal(status) && <span className="h-1.5 w-1.5 animate-breathe rounded-full bg-current" />}
      {status}
    </span>
  );
}

export function PassBadge({ pass, tolerance }: { pass: boolean | null | undefined; tolerance?: number }) {
  if (pass == null)
    return <span className="rounded-full border border-line px-2.5 py-0.5 text-xs text-faint">no truth</span>;
  const tol = tolerance != null ? ` ±${tolerance} mm` : "";
  return pass ? (
    <span className="rounded-full border border-success/30 bg-success/10 px-2.5 py-0.5 text-xs font-semibold text-success">
      PASS{tol}
    </span>
  ) : (
    <span className="rounded-full border border-danger/30 bg-danger/10 px-2.5 py-0.5 text-xs font-semibold text-danger">
      FAIL{tol}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export function Panel({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-line bg-surface/60 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Notice({ tone, children }: { tone: "info" | "warn" | "error"; children: ReactNode }) {
  const cls =
    tone === "error"
      ? "border-danger/40 bg-danger/10 text-danger"
      : tone === "warn"
        ? "border-warn/40 bg-warn/10 text-warn"
        : "border-cyan/30 bg-cyan/10 text-cyan";
  return <div className={`rounded-xl border px-4 py-3 text-sm ${cls}`}>{children}</div>;
}

export function KVTable({ data, mono = true }: { data: Record<string, unknown>; mono?: boolean }) {
  const keys = Object.keys(data).sort();
  if (keys.length === 0) return <p className="text-sm text-faint">empty</p>;
  return (
    <table className="w-full text-sm">
      <tbody>
        {keys.map((k) => (
          <tr key={k} className="border-b border-line last:border-0">
            <td className="py-1 pr-4 align-top font-mono text-xs text-muted">{k}</td>
            <td className={`break-all py-1 text-right ${mono ? "font-mono text-xs" : ""} text-ink`}>{fmtValue(data[k])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Stage timeline
// ---------------------------------------------------------------------------

/** Display order; unknown stages from the backend are appended after these. */
export const STAGE_ORDER = ["upload", "extract", "download", "reconstruct", "upload_worker", "measure", "cut"];

const STAGE_LABEL: Record<string, string> = {
  upload: "upload",
  extract: "extract (ffmpeg)",
  download: "download",
  reconstruct: "reconstruct",
  upload_worker: "upload (worker)",
  measure: "measure",
  cut: "cut",
};

export function orderStages(stages: Record<string, number>): [string, number][] {
  const keys = Object.keys(stages);
  const known = STAGE_ORDER.filter((k) => keys.includes(k));
  const rest = keys.filter((k) => !STAGE_ORDER.includes(k)).sort();
  return [...known, ...rest].map((k) => [k, stages[k]]);
}

export function StageTimeline({ timings }: { timings: AdminTimings }) {
  const rows = orderStages(timings.stages ?? {});
  const total = timings.total_s ?? rows.reduce((a, [, v]) => a + v, 0);
  const target = timings.target_s ?? 120;
  const scale = Math.max(total, target) || 1;

  return (
    <div>
      {/* stacked bar vs target */}
      <div className="relative h-5 w-full overflow-hidden rounded-md bg-white/[0.04]">
        <div className="absolute inset-y-0 left-0 flex" style={{ width: `${(total / scale) * 100}%` }}>
          {rows.map(([k, v], i) => (
            <div
              key={k}
              title={`${STAGE_LABEL[k] ?? k}: ${fmtSec(v)}`}
              className={`h-full border-r border-base/60 ${k === timings.bottleneck ? "bg-warn" : i % 2 ? "bg-accent/70" : "bg-accent/45"}`}
              style={{ width: `${total ? (v / total) * 100 : 0}%` }}
            />
          ))}
        </div>
        <div
          className="absolute inset-y-0 w-px bg-danger"
          style={{ left: `${(target / scale) * 100}%` }}
          title={`target ${target}s (FR-11)`}
        />
      </div>

      <table className="mt-3 w-full text-sm">
        <tbody>
          {rows.map(([k, v]) => {
            const bottleneck = k === timings.bottleneck;
            return (
              <tr key={k} className={`border-b border-line last:border-0 ${bottleneck ? "text-warn" : ""}`}>
                <td className="py-1 font-mono text-xs">{STAGE_LABEL[k] ?? k}</td>
                <td className="py-1 text-right font-mono text-xs">{fmtSec(v)}</td>
                <td className="w-1/2 py-1 pl-3">
                  <div className="h-1.5 w-full rounded bg-white/[0.04]">
                    <div className={`h-full rounded ${bottleneck ? "bg-warn" : "bg-accent/70"}`} style={{ width: `${total ? (v / total) * 100 : 0}%` }} />
                  </div>
                </td>
                <td className="py-1 pl-2 text-right text-xs text-faint">{bottleneck ? "bottleneck" : ""}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td className="py-1 text-xs text-faint">no stage timings yet</td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr className="border-t border-line-strong font-semibold">
            <td className="py-1.5 text-xs">total</td>
            <td className={`py-1.5 text-right font-mono text-xs ${timings.within_budget ? "text-success" : "text-danger"}`}>{fmtSec(total)}</td>
            <td className="py-1.5 pl-3 text-xs text-muted" colSpan={2}>
              target {target}s (FR-11) · {timings.within_budget ? "within budget" : `over by ${fmtSec(total - target)}`}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diameter vs height profile
// ---------------------------------------------------------------------------

export type ProfilePoint = [number, number | null];

export function ProfileChart({ profile, sliceHeight, baseDiameter }: { profile: ProfilePoint[]; sliceHeight: number | null; baseDiameter: number | null }) {
  const valid = profile.filter((p): p is [number, number] => p[1] != null && Number.isFinite(p[1]));
  if (valid.length === 0) return <p className="text-sm text-faint">no diameter profile</p>;

  const W = 560;
  const H = 220;
  const L = 48;
  const R = 16;
  const T = 14;
  const B = 34;

  const hs = profile.map((p) => p[0]);
  const ds = valid.map((p) => p[1]);
  const minH = Math.min(...hs, sliceHeight ?? Infinity);
  const maxH = Math.max(...hs, sliceHeight ?? -Infinity);
  const minD = Math.min(...ds, baseDiameter ?? Infinity);
  const maxD = Math.max(...ds, baseDiameter ?? -Infinity);
  const padD = (maxD - minD) * 0.12 || 1;
  const dLo = minD - padD;
  const dHi = maxD + padD;
  const x = (h: number) => L + ((h - minH) / (maxH - minH || 1)) * (W - L - R);
  const y = (d: number) => T + (1 - (d - dLo) / (dHi - dLo || 1)) * (H - T - B);

  // Break the polyline at nulls.
  const segments: string[] = [];
  let cur: string[] = [];
  for (const [h, d] of profile) {
    if (d == null || !Number.isFinite(d)) {
      if (cur.length) segments.push(cur.join(" "));
      cur = [];
    } else cur.push(`${x(h).toFixed(1)},${y(d).toFixed(1)}`);
  }
  if (cur.length) segments.push(cur.join(" "));

  const yTicks = 4;
  const xTicks = 6;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 260 }}>
      {/* grid + y axis */}
      {Array.from({ length: yTicks + 1 }, (_, i) => {
        const d = dLo + ((dHi - dLo) * i) / yTicks;
        return (
          <g key={i}>
            <line x1={L} x2={W - R} y1={y(d)} y2={y(d)} stroke="rgba(255,255,255,0.07)" />
            <text x={L - 6} y={y(d) + 4} textAnchor="end" fontSize="11" fill="#7C8794">
              {d.toFixed(1)}
            </text>
          </g>
        );
      })}
      {Array.from({ length: xTicks + 1 }, (_, i) => {
        const h = minH + ((maxH - minH) * i) / xTicks;
        return (
          <text key={i} x={x(h)} y={H - B + 16} textAnchor="middle" fontSize="11" fill="#7C8794">
            {h.toFixed(1)}
          </text>
        );
      })}
      <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="#9AA5B2">
        height above skin (mm)
      </text>
      <text x={12} y={T + 4} fontSize="11" fill="#9AA5B2">
        Ø mm
      </text>

      {/* base diameter reference */}
      {baseDiameter != null && Number.isFinite(baseDiameter) && (
        <line x1={L} x2={W - R} y1={y(baseDiameter)} y2={y(baseDiameter)} stroke="rgba(154,165,178,0.5)" strokeDasharray="4 4" />
      )}

      {/* slice height marker */}
      {sliceHeight != null && Number.isFinite(sliceHeight) && (
        <g>
          <line x1={x(sliceHeight)} x2={x(sliceHeight)} y1={T} y2={H - B} stroke="#FBBF24" strokeWidth={1.5} strokeDasharray="3 3" />
          <text x={x(sliceHeight) + 4} y={T + 12} fontSize="11" fill="#FBBF24">
            slice {sliceHeight.toFixed(2)} mm
          </text>
        </g>
      )}

      {segments.map((pts, i) => (
        <polyline key={i} points={pts} fill="none" stroke="#2DD4BF" strokeWidth={2} strokeLinejoin="round" />
      ))}
      {valid.map(([h, d], i) => (
        <circle key={i} cx={x(h)} cy={y(d)} r={2} fill="#2DD4BF" />
      ))}
    </svg>
  );
}
