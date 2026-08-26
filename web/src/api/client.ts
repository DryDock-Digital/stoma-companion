import type { JobStatus, ScanResult } from "../lib/flow";
import { sampleResult } from "../lib/sample";

export interface ScanStatus {
  id: string;
  status: JobStatus;
  result?: ScanResult | null;
  error?: string | null;
  /** true when this run is simulated locally (no backend configured). */
  simulated?: boolean;
}

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

/** With no backend configured (or VITE_SIMULATE=true) the flow runs locally so the
 *  app is fully demoable. Set VITE_API_BASE to talk to the real service. */
export function isSimulated(): boolean {
  return !API_BASE || import.meta.env.VITE_SIMULATE === "true";
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("aborted", "AbortError"));
    });
  });

async function createScan(video: Blob): Promise<ScanStatus> {
  const form = new FormData();
  form.append("video", video, "scan.webm");
  // upload retry: transient network failures shouldn't lose the scan (P3-3)
  let lastErr: unknown;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(`${API_BASE}/scans`, { method: "POST", body: form });
      if (!res.ok) throw new Error(`upload failed (${res.status})`);
      return (await res.json()) as ScanStatus;
    } catch (err) {
      lastErr = err;
      await sleep(600 * (attempt + 1));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("upload failed");
}

async function getScan(id: string): Promise<ScanStatus> {
  const res = await fetch(`${API_BASE}/scans/${id}`);
  if (!res.ok) throw new Error(`status ${res.status}`);
  return (await res.json()) as ScanStatus;
}

const TERMINAL: JobStatus[] = ["done", "failed"];

/**
 * Drive one scan to completion, reporting every status change via `onUpdate`.
 * Simulated unless a backend is configured. Tolerates network drops: transient
 * poll failures are retried and only surface after several consecutive misses.
 */
export async function runScan(
  video: Blob | null,
  onUpdate: (s: ScanStatus) => void,
  signal?: AbortSignal,
): Promise<ScanStatus> {
  if (isSimulated() || !video) return simulate(onUpdate, signal);

  const created = await createScan(video);
  onUpdate(created);

  let misses = 0;
  for (;;) {
    await sleep(1500, signal);
    try {
      const s = await getScan(created.id);
      misses = 0;
      onUpdate(s);
      if (TERMINAL.includes(s.status)) return s;
    } catch (err) {
      if (signal?.aborted) throw err;
      // network drop — keep trying, surface only after ~10 consecutive misses
      if (++misses >= 10) throw new Error("Lost connection. Please check your network.");
    }
  }
}

// Simulated run: step through the lifecycle on a timer, then hand back a sample.
async function simulate(
  onUpdate: (s: ScanStatus) => void,
  signal?: AbortSignal,
): Promise<ScanStatus> {
  const id = "demo";
  const script: [JobStatus, number][] = [
    ["pending", 500],
    ["keyframes_ready", 900],
    ["reconstructing", 2200],
    ["mesh_ready", 700],
    ["measuring", 1600],
    ["cutting", 1400],
  ];
  for (const [status, ms] of script) {
    onUpdate({ id, status, simulated: true });
    await sleep(ms, signal);
  }
  const done: ScanStatus = { id, status: "done", result: sampleResult(), simulated: true };
  onUpdate(done);
  return done;
}
