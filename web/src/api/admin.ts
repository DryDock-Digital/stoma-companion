// Typed client for the engineer-facing /admin endpoints. Never simulates: with no
// VITE_API_BASE the admin screen shows a "no backend configured" notice instead.

import type { JobStatus } from "../lib/flow";

export type { JobStatus };

export interface AdminScanSummary {
  id: string;
  created_at: string;
  updated_at: string;
  status: JobStatus;
  model_name: string | null;
  /** Caliper truth for the widest span (mm). */
  truth_mm: number | null;
  /** Caliper truth for the narrowest span (mm). */
  truth_min_mm: number | null;
  reference_point: string | null;
  /** Widest caliper span of the base outline (mm). */
  diameter_mm: number | null;
  /** Narrowest caliper span of the base outline (mm). */
  min_width_mm: number | null;
  /** widest − truth_mm */
  deviation_mm: number | null;
  /** narrowest − truth_min_mm */
  deviation_min_mm: number | null;
  /** true only when EVERY provided truth is within ±tolerance_mm; null when no truth. */
  within_tolerance: boolean | null;
  tolerance_mm: number;
  total_s: number | null;
  engine: string | null;
  error: string | null;
  attempts: number;
}

export interface AdminTimings {
  stages: Record<string, number>;
  total_s: number;
  target_s: number;
  within_budget: boolean;
  bottleneck: string | null;
}

export interface AdminArtifacts {
  video_url: string | null;
  mesh_url: string | null;
  poses_url: string | null;
  gcode_url: string | null;
  keyframe_urls: string[];
}

export type Json = string | number | boolean | null | Json[] | { [k: string]: Json };
export type JsonObject = Record<string, unknown>;

/** Caliper-style shape summary reported for the base outline (`result.shape`) and the
 *  wafer outline (`result.wafer_shape`). Angle 0 = the stoma's long axis; a width is the
 *  caliper span perpendicular to that direction. */
export interface ShapeSummary {
  max_width_mm: number;
  max_width_angle_deg: number;
  min_width_mm: number;
  min_width_angle_deg: number;
  equivalent_diameter_mm: number;
  perimeter_mm: number;
  area_mm2: number;
  principal_axis_deg: number;
  /** [deg, width_mm] for 0..175 step 5. */
  widths_by_angle: [number, number][];
}

/** Defensive parser: the backend contract is being implemented concurrently. */
export function parseShape(v: unknown): ShapeSummary | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const n = (k: string) => (typeof o[k] === "number" && Number.isFinite(o[k] as number) ? (o[k] as number) : NaN);
  const widths: [number, number][] = [];
  if (Array.isArray(o.widths_by_angle)) {
    for (const p of o.widths_by_angle) {
      if (Array.isArray(p) && typeof p[0] === "number" && typeof p[1] === "number" && Number.isFinite(p[1])) widths.push([p[0], p[1]]);
    }
  }
  const shape: ShapeSummary = {
    max_width_mm: n("max_width_mm"),
    max_width_angle_deg: n("max_width_angle_deg"),
    min_width_mm: n("min_width_mm"),
    min_width_angle_deg: n("min_width_angle_deg"),
    equivalent_diameter_mm: n("equivalent_diameter_mm"),
    perimeter_mm: n("perimeter_mm"),
    area_mm2: n("area_mm2"),
    principal_axis_deg: n("principal_axis_deg"),
    widths_by_angle: widths,
  };
  if (Number.isNaN(shape.max_width_mm) && Number.isNaN(shape.min_width_mm) && widths.length === 0) return null;
  return shape;
}

export interface AdminScanDetail extends AdminScanSummary {
  config: JsonObject;
  result: JsonObject | null;
  error_detail: string | null;
  error_stage: string | null;
  worker_id: string | null;
  claimed_at: string | null;
  keyframe_count: number | null;
  notes: string | null;
  timings: AdminTimings;
  artifacts: AdminArtifacts;
  run: JsonObject | null;
}

export interface NewRunInput {
  video: File;
  model_name?: string;
  truth_mm?: number | null;
  truth_min_mm?: number | null;
  reference_point?: string;
  notes?: string;
}

export interface PatchRunInput {
  model_name?: string;
  /** null clears. */
  truth_mm?: number | null;
  /** null clears. */
  truth_min_mm?: number | null;
  reference_point?: string;
  notes?: string;
}

export interface CreatedRun {
  id: string;
  status: JobStatus;
}

export interface DeletedRun {
  id: string;
  objects_deleted: number;
  run_deleted: boolean;
}

export interface ClearedRuns {
  jobs_deleted: number;
  objects_deleted: number;
  runs_deleted: number;
}

export class AdminApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null, // null = network / no response
    public readonly kind: "network" | "not_found" | "http" | "parse" = "http",
  ) {
    super(message);
    this.name = "AdminApiError";
  }
}

export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
export const hasBackend = () => API_BASE.length > 0;

export const reportCsvUrl = () => `${API_BASE}/admin/report.csv`;
export const gcodeUrl = (id: string) => `${API_BASE}/admin/scans/${encodeURIComponent(id)}/gcode`;

async function bodyMessage(res: Response): Promise<string> {
  try {
    const text = await res.text();
    try {
      const j = JSON.parse(text) as { detail?: unknown; error?: unknown };
      const d = j.detail ?? j.error;
      if (typeof d === "string") return d;
      if (d != null) return JSON.stringify(d);
    } catch {
      /* not JSON */
    }
    return text.slice(0, 300) || res.statusText;
  } catch {
    return res.statusText;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new AdminApiError(`Network error reaching ${API_BASE}${path}`, null, "network");
  }
  if (!res.ok) {
    const msg = await bodyMessage(res);
    if (res.status === 404) throw new AdminApiError(`Not found: ${path}`, 404, "not_found");
    throw new AdminApiError(`HTTP ${res.status}: ${msg}`, res.status, "http");
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new AdminApiError(`Unparseable JSON from ${path}`, res.status, "parse");
  }
}

export async function listScans(limit = 50, signal?: AbortSignal): Promise<AdminScanSummary[]> {
  const data = await request<{ jobs: AdminScanSummary[] }>(`/admin/scans?limit=${limit}`, { signal });
  return data.jobs ?? [];
}

export function getScan(id: string, signal?: AbortSignal): Promise<AdminScanDetail> {
  return request<AdminScanDetail>(`/admin/scans/${encodeURIComponent(id)}`, { signal });
}

export function patchScan(id: string, patch: PatchRunInput): Promise<AdminScanDetail> {
  return request<AdminScanDetail>(`/admin/scans/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

/** Deletes the job, its verification-log row and all stored files. 404 if missing. */
export function deleteScan(id: string): Promise<DeletedRun> {
  return request<DeletedRun>(`/admin/scans/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Deletes EVERY run. The backend requires `confirm=all`. */
export function clearAllScans(): Promise<ClearedRuns> {
  return request<ClearedRuns>(`/admin/scans?confirm=all`, { method: "DELETE" });
}

/** Creates a new job from a run's stored video, carrying over model/truths/notes. */
export function rerunScan(id: string): Promise<CreatedRun> {
  return request<CreatedRun>(`/admin/scans/${encodeURIComponent(id)}/rerun`, { method: "POST" });
}

export interface QueueHealth {
  status: string;
  queue: { counts: Record<string, number>; oldest_claim_age_s: number | null };
  keyframe_worker?: unknown;
}

export function getHealth(signal?: AbortSignal): Promise<QueueHealth> {
  return request<QueueHealth>(`/health`, { signal });
}

/** XHR so we get upload progress (fetch has no upload progress events). */
export function createScan(input: NewRunInput, onProgress?: (fraction: number) => void): Promise<CreatedRun> {
  const form = new FormData();
  form.append("video", input.video, input.video.name || "scan.mp4");
  if (input.model_name) form.append("model_name", input.model_name);
  if (input.truth_mm != null && Number.isFinite(input.truth_mm)) form.append("truth_mm", String(input.truth_mm));
  if (input.truth_min_mm != null && Number.isFinite(input.truth_min_mm)) form.append("truth_min_mm", String(input.truth_min_mm));
  if (input.reference_point) form.append("reference_point", input.reference_point);
  if (input.notes) form.append("notes", input.notes);

  return new Promise<CreatedRun>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/admin/scans`);
    xhr.responseType = "text";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new AdminApiError(`Network error uploading to ${API_BASE}/admin/scans`, null, "network"));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as CreatedRun);
        } catch {
          reject(new AdminApiError("Unparseable JSON from POST /admin/scans", xhr.status, "parse"));
        }
      } else {
        let msg = xhr.responseText.slice(0, 300);
        try {
          const j = JSON.parse(xhr.responseText) as { detail?: unknown };
          if (typeof j.detail === "string") msg = j.detail;
          else if (j.detail != null) msg = JSON.stringify(j.detail);
        } catch {
          /* keep raw text */
        }
        reject(new AdminApiError(`HTTP ${xhr.status}: ${msg}`, xhr.status, "http"));
      }
    };
    xhr.send(form);
  });
}

export function describeError(err: unknown): string {
  if (err instanceof AdminApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
