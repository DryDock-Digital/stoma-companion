import { useMemo } from "react";
import { COPY } from "../lib/copy";
import type { ScanResult } from "../lib/flow";

type Pt = [number, number];

function farthestPair(pts: Pt[]): [Pt, Pt, number] {
  let best: [Pt, Pt, number] = [pts[0], pts[0], 0];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]);
      if (d > best[2]) best = [pts[i], pts[j], d];
    }
  }
  return best;
}

/** Investor-legible 2-D view: the measured base outline, the wafer cut line, and a
 *  caliper across the widest span. No mesh, no parameters (FR-13/FR-14). */
export function OutlineChart({ result }: { result: ScanResult }) {
  const geom = useMemo(() => {
    const base = result.outline_mm ?? [];
    const wafer = result.wafer_outline_mm ?? [];
    const all = [...base, ...wafer];
    if (all.length === 0) return null;

    const xs = all.map((p) => p[0]);
    const ys = all.map((p) => p[1]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;

    const SIZE = 320;
    const PAD = 34;
    const extent = Math.max(maxX - minX, maxY - minY) || 1;
    const s = (SIZE - 2 * PAD) / extent;
    // world mm → svg px (flip y so +y is up)
    const map = ([x, y]: Pt): Pt => [SIZE / 2 + (x - cx) * s, SIZE / 2 - (y - cy) * s];
    const toPath = (poly: Pt[]) => poly.map(map).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

    const [a, b] = farthestPair(base.length ? base : wafer);
    return { SIZE, s, map, toPath, base, wafer, caliper: [map(a), map(b)] as [Pt, Pt] };
  }, [result]);

  if (!geom) return null;
  const { SIZE, s, toPath, base, wafer, caliper } = geom;
  const scaleBarPx = 10 * s; // 10 mm reference

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="h-full w-full">
      <defs>
        <linearGradient id="fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="rgba(45,212,191,0.22)" />
          <stop offset="100%" stopColor="rgba(34,211,238,0.10)" />
        </linearGradient>
      </defs>

      {/* wafer cut line (3 mm grace ring) */}
      {wafer.length > 0 && (
        <polygon
          points={toPath(wafer)}
          fill="none"
          stroke="rgba(154,165,178,0.55)"
          strokeWidth={1.5}
          strokeDasharray="5 5"
        />
      )}

      {/* measured base outline */}
      {base.length > 0 && (
        <polygon points={toPath(base)} fill="url(#fill)" stroke="#2DD4BF" strokeWidth={2.2} />
      )}

      {/* caliper across the widest span */}
      <g stroke="#EAFFFB" strokeWidth={1.3} opacity={0.9}>
        <line x1={caliper[0][0]} y1={caliper[0][1]} x2={caliper[1][0]} y2={caliper[1][1]} strokeDasharray="2 3" />
        {caliper.map((p, i) => (
          <circle key={i} cx={p[0]} cy={p[1]} r={2.4} fill="#EAFFFB" stroke="none" />
        ))}
      </g>

      {/* 10 mm scale reference */}
      <g transform={`translate(${SIZE - 34 - scaleBarPx}, ${SIZE - 24})`}>
        <line x1={0} y1={0} x2={scaleBarPx} y2={0} stroke="rgba(255,255,255,0.4)" strokeWidth={1.4} />
        <line x1={0} y1={-3} x2={0} y2={3} stroke="rgba(255,255,255,0.4)" strokeWidth={1.4} />
        <line x1={scaleBarPx} y1={-3} x2={scaleBarPx} y2={3} stroke="rgba(255,255,255,0.4)" strokeWidth={1.4} />
        <text x={scaleBarPx / 2} y={-9} textAnchor="middle" fontSize="15" fill="rgba(255,255,255,0.75)">
          {COPY.result.scaleBar}
        </text>
      </g>
    </svg>
  );
}
