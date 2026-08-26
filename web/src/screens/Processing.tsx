import { useEffect, useState } from "react";
import { runScan, type ScanStatus } from "../api/client";
import { PhaseSteps } from "../components/PhaseSteps";
import { ProgressRing } from "../components/ProgressRing";
import { RefreshIcon } from "../components/icons";
import { PHASE_COPY, phaseProgress, statusToPhase } from "../lib/flow";

export function Processing({
  video,
  onComplete,
  onRestart,
}: {
  video: Blob | null;
  onComplete: (status: ScanStatus) => void;
  onRestart: () => void;
}) {
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setError(null);
    setStatus(null);
    runScan(video, setStatus, ctrl.signal)
      .then(onComplete)
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Something went wrong");
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const phase = status ? statusToPhase(status.status) : "uploading";

  if (error) {
    return (
      <div className="animate-fade-up flex w-full max-w-sm flex-col items-center text-center">
        <div className="mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-danger/30 bg-danger/10 text-danger">
          <RefreshIcon className="h-7 w-7" />
        </div>
        <h2 className="text-xl font-semibold">{PHASE_COPY.error.label}</h2>
        <p className="mt-2 text-[15px] text-muted">{error}</p>
        <button className="btn-primary mt-7 w-full" onClick={() => setAttempt((a) => a + 1)}>
          Try again
        </button>
        <button className="btn-quiet mt-1" onClick={onRestart}>
          Start over
        </button>
      </div>
    );
  }

  return (
    <div className="animate-fade-up flex w-full max-w-sm flex-col items-center">
      <ProgressRing progress={phaseProgress(phase)} spinning>
        <div className="px-6">
          <div className="text-[13px] font-medium uppercase tracking-[0.16em] text-accent">
            Working
          </div>
          <div className="mt-1.5 text-2xl font-semibold leading-tight">{PHASE_COPY[phase].label}</div>
          <div className="mt-1 text-[13px] leading-tight text-muted">{PHASE_COPY[phase].caption}</div>
        </div>
      </ProgressRing>

      <div className="card mt-10 w-full rounded-2xl px-5 py-4">
        <PhaseSteps current={phase} />
      </div>

      <p className="mt-6 text-center text-[13px] text-faint">
        Keep the app open — this usually takes under two minutes.
      </p>
    </div>
  );
}
