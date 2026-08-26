import type { ScanResult } from "./flow";

/**
 * A realistic sample measurement for the simulated/demo path — an irregular oval
 * stoma (~33 mm across) with a 3 mm grace-ring wafer outline. Used when no backend
 * is configured so the whole flow is demoable, and to seed the result view.
 */
function ovalOutline(
  rx: number,
  ry: number,
  points = 96,
  wobble = 0.06,
): [number, number][] {
  const pts: [number, number][] = [];
  for (let i = 0; i < points; i++) {
    const t = (i / points) * Math.PI * 2;
    // deterministic, organic-looking irregularity (no RNG → stable renders)
    const r = 1 + wobble * (Math.sin(t * 3) * 0.6 + Math.sin(t * 5 + 1.1) * 0.4);
    pts.push([Math.cos(t) * rx * r, Math.sin(t) * ry * r]);
  }
  return pts;
}

function offsetOutward(outline: [number, number][], mm: number): [number, number][] {
  const cx = outline.reduce((s, p) => s + p[0], 0) / outline.length;
  const cy = outline.reduce((s, p) => s + p[1], 0) / outline.length;
  return outline.map(([x, y]) => {
    const dx = x - cx;
    const dy = y - cy;
    const d = Math.hypot(dx, dy) || 1;
    return [x + (dx / d) * mm, y + (dy / d) * mm] as [number, number];
  });
}

export function sampleResult(): ScanResult {
  const outline = ovalOutline(16.5, 13.2); // ~33 × 26 mm
  return {
    diameter_mm: 33.0,
    deviation_mm: 0.4,
    tolerance_mm: 1.0,
    within_tolerance: true,
    outline_mm: outline,
    wafer_outline_mm: offsetOutward(outline, 3.0), // 3 mm grace ring (FR-07)
    engine: "colmap+openmvs",
  };
}
