import type { ScanStatus } from "../api/client";
import { OutlineChart } from "../components/OutlineChart";
import { CheckIcon, RefreshIcon } from "../components/icons";
import { COPY } from "../lib/copy";

/** Headline is driven by job status and (once P4 lands) `result.cut` — never hard-coded. */
function headlineFor(status: ScanStatus): { title: string; sub?: string } {
  const h = COPY.result.headline;
  const cut = status.result?.cut;
  if (cut) {
    switch (cut.status) {
      case "queued":
        return { title: h.cutQueued, sub: h.cutQueuedSub };
      case "cutting":
        return { title: h.cutting, sub: h.cuttingSub };
      case "done":
        return { title: h.cutDone };
      case "failed":
        return { title: h.cutFailed, sub: h.cutFailedSub };
    }
  }
  if (status.status === "done") return { title: h.done };
  return { title: h.measured, sub: h.measuredSub };
}

export function Result({
  status,
  onRestart,
  onRescan,
}: {
  status: ScanStatus;
  onRestart: () => void;
  onRescan: () => void;
}) {
  // Only these fields may reach the patient (FR-13): diameter, tolerance,
  // within_tolerance, the two outlines. engine / timings / gcode / deviation never render.
  const result = status.result ?? undefined;
  const diameter = result?.diameter_mm;
  const tol = result?.tolerance_mm ?? 1;
  const within = result?.within_tolerance; // null on real scans → no badge either way

  if (within === false) {
    return (
      <div className="animate-fade-up flex w-full max-w-sm flex-col items-center text-center">
        <div className="mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-warn/30 bg-warn/10 text-warn">
          <RefreshIcon className="h-7 w-7" />
        </div>
        <h2 className="text-2xl font-semibold">{COPY.result.rescanTitle}</h2>
        <p className="mt-3 text-lg leading-relaxed text-muted">{COPY.result.rescanBody}</p>
        <button className="btn-primary mt-7 w-full" onClick={onRescan}>
          {COPY.result.scanAgain}
        </button>
        <button className="btn-quiet mt-2 w-full" onClick={onRestart}>
          {COPY.processing.startOver}
        </button>
      </div>
    );
  }

  const headline = headlineFor(status);

  return (
    <div className="animate-fade-up flex w-full max-w-sm flex-col items-center">
      <div className="mb-1 grid h-14 w-14 animate-scale-in place-items-center rounded-full border border-accent/40 bg-accent/12 text-accent">
        <CheckIcon className="h-7 w-7" />
      </div>
      <p className="eyebrow mt-3 text-center">{headline.title}</p>
      {headline.sub && <p className="mt-1 text-center text-base text-muted">{headline.sub}</p>}

      {/* headline measurement */}
      <div className="mt-2 flex items-end gap-1.5">
        <span className="text-6xl font-semibold tracking-tight tabular-nums text-gradient">
          {diameter != null ? diameter.toFixed(1) : "—"}
        </span>
        <span className="mb-2 text-xl font-medium text-muted">mm</span>
      </div>
      <p className="text-base text-muted">{COPY.result.diameterLabel}</p>

      {within === true && diameter != null && (
        <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-3 py-1 text-base font-medium text-success">
          <CheckIcon className="h-4 w-4" />
          {COPY.result.withinTolerance(tol)}
        </div>
      )}

      {/* outline + wafer overlay */}
      {result?.outline_mm && (
        <div className="card mt-7 w-full overflow-hidden rounded-3xl p-3">
          <div className="aspect-square w-full">
            <OutlineChart result={result} />
          </div>
          <div className="flex items-center justify-center gap-5 border-t border-line px-2 pb-1 pt-3 text-base text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-accent" /> {COPY.result.legendBase}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0 w-4 border-t-2 border-dashed border-muted" />{" "}
              {COPY.result.legendWafer}
            </span>
          </div>
        </div>
      )}

      <div className="mt-8 w-full">
        <button className="btn-primary w-full" onClick={onRestart}>
          {COPY.result.newScan}
        </button>
      </div>
    </div>
  );
}
