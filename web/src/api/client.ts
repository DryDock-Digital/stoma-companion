import { COPY } from "../lib/copy";
import { isTerminal, type JobStatus, type ScanResult } from "../lib/flow";
import { sampleResult } from "../lib/sample";
import { clearScanId, saveScanId } from "../lib/storage";

export interface ScanStatus {
  id: string;
  status: JobStatus;
  result?: ScanResult | null;
  /** Patient-safe sentence from the backend when status=failed. Display as-is. */
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  /** true when this run is simulated locally (no backend configured). */
  simulated?: boolean;
}

/** Narrow contract: upload a video, poll a job. Real and simulated implementations
 *  share it so the simulation can be deleted without touching the real path. */
export interface ScanApi {
  readonly simulated: boolean;
  upload(video: Blob, signal?: AbortSignal): Promise<ScanStatus>;
  poll(id: string, signal?: AbortSignal): Promise<ScanStatus>;
}

/** Error whose message is already safe to show a patient. */
export class ScanError extends Error {
  constructor(
    message: string,
    public readonly kind: "upload" | "network" | "timeout" | "no_video" = "network",
  ) {
    super(message);
    this.name = "ScanError";
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

/** Simulated only when no backend is configured, or VITE_SIMULATE=true is explicit.
 *  With a backend configured and no VITE_SIMULATE flag the app NEVER simulates. */
export function isSimulated(): boolean {
  return !API_BASE || import.meta.env.VITE_SIMULATE === "true";
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("aborted", "AbortError"));
    });
  });

// ---------------------------------------------------------------------------
// Real backend
// ---------------------------------------------------------------------------

function extensionFor(mime: string): string {
  const base = mime.split(";")[0].trim().toLowerCase();
  switch (base) {
    case "video/mp4":
      return "mp4";
    case "video/webm":
      return "webm";
    case "video/quicktime":
      return "mov";
    default:
      return "bin";
  }
}

function uploadErrorCopy(status: number): string {
  if (status === 413) return COPY.errors.tooLarge;
  if (status === 415 || status === 422 || status === 400) return COPY.errors.badVideo;
  return COPY.errors.generic;
}

const UPLOAD_ATTEMPTS = 3;

class RealScanApi implements ScanApi {
  readonly simulated = false;

  async upload(video: Blob, signal?: AbortSignal): Promise<ScanStatus> {
    const form = new FormData();
    form.append("video", video, `scan.${extensionFor(video.type)}`);

    // Retry only network errors and 5xx (max 3, backoff). 4xx fails immediately.
    let lastErr: unknown;
    for (let attempt = 0; attempt < UPLOAD_ATTEMPTS; attempt++) {
      let res: Response;
      try {
        res = await fetch(`${API_BASE}/scans`, { method: "POST", body: form, signal });
      } catch (err) {
        if (signal?.aborted) throw err;
        lastErr = new ScanError(COPY.errors.network, "upload");
        await sleep(800 * (attempt + 1), signal);
        continue;
      }
      if (res.ok) return (await res.json()) as ScanStatus;
      if (res.status >= 500) {
        lastErr = new ScanError(COPY.errors.server, "upload");
        await sleep(800 * (attempt + 1), signal);
        continue;
      }
      throw new ScanError(uploadErrorCopy(res.status), "upload");
    }
    throw lastErr instanceof Error ? lastErr : new ScanError(COPY.errors.network, "upload");
  }

  async poll(id: string, signal?: AbortSignal): Promise<ScanStatus> {
    const res = await fetch(`${API_BASE}/scans/${encodeURIComponent(id)}`, { signal });
    if (!res.ok) throw new Error(`status ${res.status}`);
    return (await res.json()) as ScanStatus;
  }
}

// ---------------------------------------------------------------------------
// Simulation (demo only — delete this class and the branch in `getScanApi`)
// ---------------------------------------------------------------------------

const SIM_SCRIPT: [JobStatus, number][] = [
  ["pending", 500],
  ["keyframes_ready", 900],
  ["reconstructing", 2200],
  ["mesh_ready", 700],
  ["measuring", 1600],
  ["cutting", 1400],
  ["done", 0],
];

class SimulatedScanApi implements ScanApi {
  readonly simulated = true;
  private step = 0;

  async upload(): Promise<ScanStatus> {
    this.step = 0;
    return { id: "demo", status: "pending", simulated: true };
  }

  async poll(id: string, signal?: AbortSignal): Promise<ScanStatus> {
    const [, waitMs] = SIM_SCRIPT[Math.min(this.step, SIM_SCRIPT.length - 1)];
    await sleep(waitMs, signal);
    this.step = Math.min(this.step + 1, SIM_SCRIPT.length - 1);
    const [status] = SIM_SCRIPT[this.step];
    if (status === "done") return { id, status, result: sampleResult(), simulated: true };
    return { id, status, simulated: true };
  }
}

let api: ScanApi | null = null;
export function getScanApi(): ScanApi {
  if (!api) api = isSimulated() ? new SimulatedScanApi() : new RealScanApi();
  return api;
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_MISSES = 10;
/** Overall ceiling for one job (FR-11 target is 2 min; 10 min is "something is wrong"). */
export const POLL_CEILING_MS = 10 * 60 * 1000;

/**
 * Drive one scan to a terminal state, reporting every status change via `onUpdate`.
 * Pass `resumeId` to pick up an in-flight job (phone lock) instead of re-uploading.
 * The scan id is persisted while the job runs and cleared once terminal.
 */
export async function runScan(
  video: Blob | null,
  onUpdate: (s: ScanStatus) => void,
  opts: { signal?: AbortSignal; resumeId?: string | null } = {},
): Promise<ScanStatus> {
  const { signal, resumeId } = opts;
  const scanApi = getScanApi();

  let id: string;
  if (resumeId) {
    id = resumeId;
  } else {
    if (!video) throw new ScanError(COPY.errors.noVideo, "no_video");
    const created = await scanApi.upload(video, signal);
    created.simulated = scanApi.simulated;
    if (!scanApi.simulated) saveScanId(created.id);
    onUpdate(created);
    if (isTerminal(created.status)) {
      clearScanId();
      return created;
    }
    id = created.id;
  }

  const startedAt = Date.now();
  let misses = 0;
  for (;;) {
    if (!scanApi.simulated) await sleep(POLL_INTERVAL_MS, signal);
    if (Date.now() - startedAt > POLL_CEILING_MS) throw new ScanError(COPY.errors.timeout, "timeout");
    try {
      const s = await scanApi.poll(id, signal);
      s.simulated = scanApi.simulated;
      misses = 0;
      onUpdate(s);
      if (isTerminal(s.status)) {
        clearScanId();
        return s;
      }
    } catch (err) {
      if (signal?.aborted) throw err;
      // network drop — keep trying, surface only after several consecutive misses
      if (++misses >= POLL_MAX_MISSES) throw new ScanError(COPY.errors.lostConnection, "network");
    }
  }
}
