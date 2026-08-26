// Caliper width by direction (line chart) for the base and wafer outlines, with the
// ±tolerance bands around the caliper truths. Engineer-facing only.

import type { ShapeSummary } from "../../api/admin";

export interface WidthChartProps {
  base: ShapeSummary | null;
  wafer: ShapeSummary | null;
  truthMax: number | null;
  truthMin: number | null;
  tolerance: number;
}

const BASE_COLOR = "#2DD4BF";
const WAFER_COLOR = "#9AA5B2";
const MAX_COLOR = "#FBBF24";
const MIN_COLOR = "#F472B6";

export function WidthChart({ base, wafer, truthMax, truthMin, tolerance }: WidthChartProps) {
  const series = [
    { key: "base", label: "base", color: BASE_COLOR, shape: base, dash: undefined as string | undefined },
    { key: "wafer", label: "wafer", color: WAFER_COLOR, shape: wafer, dash: "5 4" },
  ].filter((s) => s.shape && s.shape.widths_by_angle.length > 0);
  if (series.length === 0) return <p className="text-sm text-faint">no width-by-direction data</p>;

  const W = 560;
  const H = 240;
  const L = 48;
  const R = 16;
  const T = 14;
  const B = 34;

  const ws: number[] = [];
  for (const s of series) for (const [, w] of s.shape!.widths_by_angle) ws.push(w);
  const truths = [truthMax, truthMin].filter((t): t is number => t != null && Number.isFinite(t));
  for (const t of truths) ws.push(t - tolerance, t + tolerance);
  const minW = Math.min(...ws);
  const maxW = Math.max(...ws);
  const pad = (maxW - minW) * 0.12 || 1;
  const lo = minW - pad;
  const hi = maxW + pad;

  const x = (deg: number) => L + (deg / 180) * (W - L - R);
  const y = (w: number) => T + (1 - (w - lo) / (hi - lo || 1)) * (H - T - B);

  const yTicks = 4;
  const xTicks = [0, 30, 60, 90, 120, 150, 180];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 280 }}>
        {/* ±tolerance bands around truths */}
        {truthMax != null && Number.isFinite(truthMax) && (
          <g>
            <rect x={L} width={W - L - R} y={y(truthMax + tolerance)} height={Math.max(0, y(truthMax - tolerance) - y(truthMax + tolerance))} fill={MAX_COLOR} opacity={0.12} />
            <line x1={L} x2={W - R} y1={y(truthMax)} y2={y(truthMax)} stroke={MAX_COLOR} strokeDasharray="4 4" opacity={0.7} />
            <text x={W - R - 2} y={y(truthMax) - 4} textAnchor="end" fontSize="10" fill={MAX_COLOR}>
              widest truth {truthMax.toFixed(2)} ±{tolerance}
            </text>
          </g>
        )}
        {truthMin != null && Number.isFinite(truthMin) && (
          <g>
            <rect x={L} width={W - L - R} y={y(truthMin + tolerance)} height={Math.max(0, y(truthMin - tolerance) - y(truthMin + tolerance))} fill={MIN_COLOR} opacity={0.12} />
            <line x1={L} x2={W - R} y1={y(truthMin)} y2={y(truthMin)} stroke={MIN_COLOR} strokeDasharray="4 4" opacity={0.7} />
            <text x={W - R - 2} y={y(truthMin) + 12} textAnchor="end" fontSize="10" fill={MIN_COLOR}>
              narrowest truth {truthMin.toFixed(2)} ±{tolerance}
            </text>
          </g>
        )}

        {/* grid + axes */}
        {Array.from({ length: yTicks + 1 }, (_, i) => {
          const w = lo + ((hi - lo) * i) / yTicks;
          return (
            <g key={i}>
              <line x1={L} x2={W - R} y1={y(w)} y2={y(w)} stroke="rgba(255,255,255,0.07)" />
              <text x={L - 6} y={y(w) + 4} textAnchor="end" fontSize="11" fill="#7C8794">
                {w.toFixed(1)}
              </text>
            </g>
          );
        })}
        {xTicks.map((d) => (
          <text key={d} x={x(d)} y={H - B + 16} textAnchor="middle" fontSize="11" fill="#7C8794">
            {d}°
          </text>
        ))}
        <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="#9AA5B2">
          direction from long axis (°)
        </text>
        <text x={12} y={T + 4} fontSize="11" fill="#9AA5B2">
          width mm
        </text>

        {/* series */}
        {series.map((s) => {
          const sh = s.shape!;
          const pts = [...sh.widths_by_angle].sort((a, b) => a[0] - b[0]);
          return (
            <g key={s.key}>
              <polyline points={pts.map(([d, w]) => `${x(d).toFixed(1)},${y(w).toFixed(1)}`).join(" ")} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} strokeLinejoin="round" />
              {Number.isFinite(sh.max_width_mm) && Number.isFinite(sh.max_width_angle_deg) && (
                <g>
                  <circle cx={x(norm(sh.max_width_angle_deg))} cy={y(sh.max_width_mm)} r={3.5} fill={s.color} stroke="#0b0f14" />
                  {s.key === "base" && (
                    <text x={x(norm(sh.max_width_angle_deg))} y={y(sh.max_width_mm) - 8} textAnchor="middle" fontSize="10" fill={s.color}>
                      max {sh.max_width_mm.toFixed(2)} @ {Math.round(norm(sh.max_width_angle_deg))}°
                    </text>
                  )}
                </g>
              )}
              {Number.isFinite(sh.min_width_mm) && Number.isFinite(sh.min_width_angle_deg) && (
                <g>
                  <circle cx={x(norm(sh.min_width_angle_deg))} cy={y(sh.min_width_mm)} r={3.5} fill={s.color} stroke="#0b0f14" />
                  {s.key === "base" && (
                    <text x={x(norm(sh.min_width_angle_deg))} y={y(sh.min_width_mm) + 14} textAnchor="middle" fontSize="10" fill={s.color}>
                      min {sh.min_width_mm.toFixed(2)} @ {Math.round(norm(sh.min_width_angle_deg))}°
                    </text>
                  )}
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-faint">
        {series.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-5" style={{ background: s.color }} /> {s.label}
          </span>
        ))}
        <span>angle 0° = stoma long axis · width = caliper span perpendicular to that direction</span>
      </div>
    </div>
  );
}

/** Fold an angle into [0, 180). */
function norm(deg: number): number {
  const d = ((deg % 180) + 180) % 180;
  return d;
}
