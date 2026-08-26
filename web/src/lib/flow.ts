// Job lifecycle (mirrors backend app/models.py JobStatus) → patient-facing phases.

import { COPY } from "./copy";

export type JobStatus =
  | "pending"
  | "extracting"
  | "keyframes_ready"
  | "reconstructing"
  | "mesh_ready"
  | "measuring"
  | "measured"
  | "cutting"
  | "done"
  | "failed";

/** The pipeline currently ends at `measured` (cutting lands in P4); `done` = cut finished. */
export const SUCCESS_TERMINAL: readonly JobStatus[] = ["measured", "done"];
export const TERMINAL: readonly JobStatus[] = [...SUCCESS_TERMINAL, "failed"];

export const isTerminal = (s: JobStatus) => TERMINAL.includes(s);
export const isSuccess = (s: JobStatus) => SUCCESS_TERMINAL.includes(s);

export type Phase = "uploading" | "reconstructing" | "measuring" | "cutting" | "done" | "error";

// The visible progress track (FR-12). `error` is off-track.
export const PHASES: Phase[] = ["uploading", "reconstructing", "measuring", "cutting", "done"];

// Plain language, no jargon — see lib/copy.ts (single review point).
export const PHASE_COPY: Record<Phase, { label: string; caption: string }> = COPY.phases;

export function statusToPhase(status: JobStatus): Phase {
  switch (status) {
    case "pending":
    case "extracting":
      return "uploading";
    case "keyframes_ready":
    case "reconstructing":
      return "reconstructing";
    case "mesh_ready":
    case "measuring":
    case "measured":
      return "measuring";
    case "cutting":
      return "cutting";
    case "done":
      return "done";
    case "failed":
      return "error";
  }
}

/** Phase plus whether that phase has finished (e.g. `measured` = measuring complete,
 *  cutting is "next"). Lets the stepper show a completed step instead of skipping it. */
export function statusToPhaseView(status: JobStatus): { phase: Phase; complete: boolean } {
  return { phase: statusToPhase(status), complete: status === "measured" || status === "done" };
}

/** 0..1 progress across the visible track for a phase. */
export function phaseProgress(phase: Phase, complete = false): number {
  if (phase === "error") return 0;
  const i = PHASES.indexOf(phase);
  return (i + (complete ? 1 : 0.5)) / PHASES.length;
}

export type CutStatus = "queued" | "cutting" | "done" | "failed";

/** Backend result JSON. Only diameter, tolerance, within_tolerance, the outlines and
 *  `cut` may reach the patient UI; the rest is never rendered (FR-13). */
export interface ScanResult {
  diameter_mm?: number;
  deviation_mm?: number | null;
  tolerance_mm?: number;
  /** null on every real patient scan — only set when a caliper truth was provided. */
  within_tolerance?: boolean | null;
  /** Base perimeter, mm, in the slice plane. */
  outline_mm?: [number, number][];
  /** Wafer cut line = base + configurable grace ring (FR-07). */
  wafer_outline_mm?: [number, number][];
  engine?: string;
  scale_mm_per_unit?: number;
  gcode_path?: string;
  timings_s?: Record<string, number>;
  /** Added by P4 — absent until the cutting stage exists. */
  cut?: { status: CutStatus } | null;
}
