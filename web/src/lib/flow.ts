// Job lifecycle (mirrors backend app/models.py JobStatus) → patient-facing phases.

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

export type Phase = "uploading" | "reconstructing" | "measuring" | "cutting" | "done" | "error";

// The visible progress track (FR-12). `error` is off-track.
export const PHASES: Phase[] = ["uploading", "reconstructing", "measuring", "cutting", "done"];

// Plain language, no jargon — 48% of patients are over 60 (NFR-05).
export const PHASE_COPY: Record<Phase, { label: string; caption: string }> = {
  uploading: { label: "Uploading", caption: "Sending your scan securely" },
  reconstructing: { label: "Building model", caption: "Rebuilding your stoma in 3D" },
  measuring: { label: "Measuring", caption: "Finding the exact shape and size" },
  cutting: { label: "Cutting", caption: "Shaping your wafer to fit" },
  done: { label: "Ready", caption: "Your wafer is ready" },
  error: { label: "We hit a snag", caption: "Let’s try that again" },
};

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

/** 0..1 progress across the visible track for a phase. */
export function phaseProgress(phase: Phase): number {
  if (phase === "error") return 0;
  const i = PHASES.indexOf(phase);
  return (i + 1) / PHASES.length;
}

export interface ScanResult {
  diameter_mm?: number;
  deviation_mm?: number;
  tolerance_mm?: number;
  within_tolerance?: boolean;
  /** Base perimeter, mm, in the slice plane. */
  outline_mm?: [number, number][];
  /** Wafer cut line = base + configurable grace ring (FR-07). */
  wafer_outline_mm?: [number, number][];
  engine?: string;
}
