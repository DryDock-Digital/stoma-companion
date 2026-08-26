// Print-at-1:1 outline sheet for the test bench: #/run/<id>/print. The SVG's width and
// height are in mm with a mm viewBox, so at 100 % / actual size it prints at true scale.
// Engineer-facing only; never reachable from the patient flow.

import { useEffect, useMemo, useState } from "react";
import { describeError, getScan, parseShape, type AdminScanDetail } from "../api/admin";

type Pt = [number, number];

const PAGE_W = 120; // mm
const PAGE_H = 150; // mm
const HEADER_H = 26; // mm reserved at the top for text
const FOOTER_H = 22; // mm reserved at the bottom for scale bars + note

function pointList(v: unknown): Pt[] {
  if (!Array.isArray(v)) return [];
  const out: Pt[] = [];
  for (const p of v) if (Array.isArray(p) && typeof p[0] === "number" && typeof p[1] === "number") out.push([p[0], p[1]]);
  return out;
}

function centroid(poly: Pt[]): Pt {
  // Area-weighted (shoelace) centroid; falls back to the vertex mean for degenerate polygons.
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i];
    const [x1, y1] = poly[(i + 1) % poly.length];
    const c = x0 * y1 - x1 * y0;
    a += c;
    cx += (x0 + x1) * c;
    cy += (y0 + y1) * c;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length || 1;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  a *= 0.5;
  return [cx / (6 * a), cy / (6 * a)];
}

/** Radial offset from the centroid — an acceptable approximation of a true parallel
 *  offset for the tolerance band on a near-convex outline. */
function radialOffset(poly: Pt[], c: Pt, d: number): Pt[] {
  return poly.map(([x, y]) => {
    const dx = x - c[0];
    const dy = y - c[1];
    const r = Math.hypot(dx, dy) || 1;
    const k = (r + d) / r;
    return [c[0] + dx * k, c[1] + dy * k];
  });
}

export function PrintOutline({ id, backHref }: { id: string; backHref: string }) {
  const [job, setJob] = useState<AdminScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    getScan(id, ac.signal)
      .then((d) => {
        setJob(d);
        setError(null);
      })
      .catch((err) => {
        if (!ac.signal.aborted) setError(describeError(err));
      });
    return () => ac.abort();
  }, [id]);

  const geom = useMemo(() => {
    const result = job?.result;
    if (!result) return null;
    const base = pointList(result.outline_mm);
    const wafer = pointList(result.wafer_outline_mm);
    const all = [...base, ...wafer];
    if (all.length === 0) return null;
    const c = base.length ? centroid(base) : centroid(wafer);
    const tol = job?.tolerance_mm ?? 1;
    const outer = base.length ? radialOffset(base, c, tol) : [];
    const inner = base.length ? radialOffset(base, c, -tol) : [];
    // Drawing area centre (mm on the page). Flip y so +y is up on paper.
    const areaTop = HEADER_H;
    const areaBottom = PAGE_H - FOOTER_H;
    const ox = PAGE_W / 2;
    const oy = (areaTop + areaBottom) / 2;
    const map = ([x, y]: Pt): Pt => [ox + (x - c[0]), oy - (y - c[1])];
    const toPts = (poly: Pt[]) => poly.map(map).map(([x, y]) => `${x.toFixed(3)},${y.toFixed(3)}`).join(" ");
    const extent = Math.max(...all.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) + tol;
    const fits = extent * 2 <= Math.min(PAGE_W - 10, areaBottom - areaTop);
    return { base, wafer, outer, inner, ox, oy, toPts, tol, fits, shape: parseShape(result.shape), waferShape: parseShape(result.wafer_shape) };
  }, [job]);

  if (error)
    return (
      <div className="p-6 text-sm text-danger">
        {error}{" "}
        <a className="underline" href={backHref}>
          back
        </a>
      </div>
    );
  if (!job) return <p className="p-6 text-sm text-faint">loading {id}…</p>;

  const widest = job.diameter_mm ?? geom?.shape?.max_width_mm ?? null;
  const narrowest = job.min_width_mm ?? geom?.shape?.min_width_mm ?? null;
  const fmt = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(2)} mm`);
  const date = new Date(job.created_at);
  const dateStr = Number.isNaN(date.getTime()) ? job.created_at : date.toLocaleString();

  return (
    <div className="print-root">
      <style>{`
        @media print {
          @page { margin: 10mm; }
          html, body, #root { background: #fff !important; background-image: none !important; height: auto !important; }
          body * { visibility: hidden; }
          .print-page, .print-page * { visibility: visible; }
          .print-page { position: absolute; left: 0; top: 0; margin: 0; box-shadow: none; }
          .print-hide { display: none !important; }
        }
        .print-page { width: ${PAGE_W}mm; height: ${PAGE_H}mm; background: #fff; color: #000; }
      `}</style>

      <div className="print-hide mb-4 flex flex-wrap items-center gap-3 text-sm">
        <a className="text-muted hover:text-ink" href={backHref}>
          ← back to run
        </a>
        <button type="button" onClick={() => window.print()} className="rounded-lg bg-accent px-4 py-1.5 font-semibold text-accent-ink">
          Print
        </button>
        <span className="text-xs text-faint">Print at 100 % / actual size (no "fit to page"); check the 50 mm bar with a ruler.</span>
        {geom && !geom.fits && <span className="text-xs text-warn">outline exceeds the {PAGE_W}×{PAGE_H} mm sheet — it will be clipped</span>}
      </div>

      {!geom ? (
        <p className="text-sm text-faint">no outline on this run yet</p>
      ) : (
        <div className="print-page mx-auto shadow-card">
          <svg width={`${PAGE_W}mm`} height={`${PAGE_H}mm`} viewBox={`0 0 ${PAGE_W} ${PAGE_H}`} xmlns="http://www.w3.org/2000/svg" style={{ display: "block" }}>
            {/* header */}
            <g fill="#000" fontFamily="ui-monospace, Menlo, monospace">
              <text x={6} y={7} fontSize={3.6} fontWeight="bold">
                Stoma Companion — 1:1 outline
              </text>
              <text x={6} y={12} fontSize={2.6}>
                model: {job.model_name ?? "—"} · run: {job.id}
              </text>
              <text x={6} y={16.5} fontSize={2.6}>
                {dateStr} · engine {job.engine ?? "—"}
              </text>
              <text x={6} y={21} fontSize={2.6}>
                widest {fmt(widest)} · narrowest {fmt(narrowest)} · perimeter {fmt(geom.shape?.perimeter_mm)} · grace ring{" "}
                {typeof job.result?.grace_ring_mm === "number" ? `${(job.result.grace_ring_mm as number).toFixed(1)} mm` : "—"}
              </text>
              {(job.truth_mm != null || job.truth_min_mm != null) && (
                <text x={6} y={25} fontSize={2.6}>
                  caliper truth: widest {fmt(job.truth_mm)} · narrowest {fmt(job.truth_min_mm)} · tolerance ±{geom.tol} mm
                </text>
              )}
            </g>
            <line x1={4} x2={PAGE_W - 4} y1={HEADER_H} y2={HEADER_H} stroke="#bbb" strokeWidth={0.2} />

            {/* ±tolerance band around the base outline (even-odd ring fill) */}
            {geom.outer.length > 0 && (
              <path d={`M ${geom.toPts(geom.outer).replace(/ /g, " L ")} Z M ${geom.toPts(geom.inner).replace(/ /g, " L ")} Z`} fill="#9ad9cf" fillOpacity={0.35} fillRule="evenodd" stroke="none" />
            )}
            {/* wafer cut line */}
            {geom.wafer.length > 0 && <polygon points={geom.toPts(geom.wafer)} fill="none" stroke="#555" strokeWidth={0.3} strokeDasharray="1.5 1" />}
            {/* base outline */}
            {geom.base.length > 0 && <polygon points={geom.toPts(geom.base)} fill="none" stroke="#000" strokeWidth={0.35} />}
            {/* centre cross */}
            <g stroke="#000" strokeWidth={0.2}>
              <line x1={geom.ox - 4} x2={geom.ox + 4} y1={geom.oy} y2={geom.oy} />
              <line x1={geom.ox} x2={geom.ox} y1={geom.oy - 4} y2={geom.oy + 4} />
            </g>

            {/* legend */}
            <g fontFamily="ui-monospace, Menlo, monospace" fontSize={2.4} fill="#000">
              <line x1={PAGE_W - 40} x2={PAGE_W - 32} y1={HEADER_H + 4} y2={HEADER_H + 4} stroke="#000" strokeWidth={0.35} />
              <text x={PAGE_W - 30} y={HEADER_H + 4.8}>base outline</text>
              <line x1={PAGE_W - 40} x2={PAGE_W - 32} y1={HEADER_H + 8} y2={HEADER_H + 8} stroke="#555" strokeWidth={0.3} strokeDasharray="1.5 1" />
              <text x={PAGE_W - 30} y={HEADER_H + 8.8}>wafer cut line</text>
              <rect x={PAGE_W - 40} y={HEADER_H + 10.5} width={8} height={3} fill="#9ad9cf" fillOpacity={0.35} />
              <text x={PAGE_W - 30} y={HEADER_H + 12.8}>±{geom.tol} mm band</text>
            </g>

            {/* scale bars */}
            <g transform={`translate(6, ${PAGE_H - FOOTER_H + 6})`} stroke="#000" strokeWidth={0.3} fontFamily="ui-monospace, Menlo, monospace" fontSize={2.4}>
              <ScaleBar length={50} label="50 mm" />
              <g transform="translate(60, 0)">
                <ScaleBar length={10} label="10 mm" />
              </g>
            </g>
            <text x={6} y={PAGE_H - 5} fontSize={2.2} fill="#333" fontFamily="ui-monospace, Menlo, monospace">
              Print at 100 % / actual size; check the 50 mm bar with a ruler.
            </text>
          </svg>
        </div>
      )}
    </div>
  );
}

function ScaleBar({ length, label }: { length: number; label: string }) {
  const ticks: number[] = [];
  for (let t = 0; t <= length; t += 10) ticks.push(t);
  return (
    <g>
      <line x1={0} x2={length} y1={0} y2={0} />
      {ticks.map((t) => (
        <g key={t}>
          <line x1={t} x2={t} y1={-2} y2={2} />
          <text x={t} y={5.5} textAnchor="middle" stroke="none" fill="#000">
            {t}
          </text>
        </g>
      ))}
      {length >= 10 &&
        Array.from({ length: length / 10 }, (_, i) => i * 10 + 5).map((t) => <line key={`h${t}`} x1={t} x2={t} y1={-1} y2={1} />)}
      <text x={length / 2} y={-3} textAnchor="middle" stroke="none" fill="#000">
        {label}
      </text>
    </g>
  );
}
