import { useEffect, useRef, useState } from "react";
import { runScan, ScanError, type ScanStatus } from "../api/client";
import { PhaseSteps } from "../components/PhaseSteps";
import { ProgressRing } from "../components/ProgressRing";
import { RefreshIcon } from "../components/icons";
import { COPY } from "../lib/copy";
import { PHASE_COPY, phaseProgress, statusToPhaseView } from "../lib/flow";

export function Processing({
  video,
  resumeId,
  onStatus,
  onComplete,
  onRetake,
  onRestart,
}: {
  video: Blob | null;
  /** In-flight job id restored from storage (phone lock) — poll it, don't re-upload. */
  resumeId?: string | null;
  onStatus?: (s: ScanStatus | null) => void;
  onComplete: (status: ScanStatus) => void;
  /** Back to Capture for a fresh recording. */
  onRetake: () => void;
  onRestart: () => void;
}) {
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Once a job exists, retries poll it again rather than re-uploading the video.
  const jobIdRef = useRef<string | null>(resumeId ?? null);
  const resumed = Boolean(resumeId);

  useEffect(() => {
    const ctrl = new AbortController();
    setError(null);
    setStatus(null);
    onStatus?.(null);
    runScan(
      video,
      (s) => {
        jobIdRef.current = s.id;
        setStatus(s);
        onStatus?.(s);
      },
      { signal: ctrl.signal, resumeId: jobIdRef.current },
    )
      .then((s) => {
        if (!ctrl.signal.aborted) onComplete(s);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof ScanError) {
          if (err.kind === "upload" || err.kind === "no_video") jobIdRef.current = null;
          setError(err.message);
        } else {
          setError(COPY.errors.generic);
        }
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const failed = status?.status === "failed";

  if (failed || error) {
    const canRetryHere = !failed && (jobIdRef.current != null || video != null);
    return (
      <div className="animate-fade-up flex w-full max-w-sm flex-col items-center text-center">
        <div className="mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-danger/30 bg-danger/10 text-danger">
          <RefreshIcon className="h-7 w-7" />
        </div>
        <h2 className="text-2xl font-semibold">
          {failed ? COPY.processing.failedTitle : PHASE_COPY.error.label}
        </h2>
        <p className="mt-3 text-lg leading-relaxed text-muted">
          {failed ? status?.error || COPY.processing.failedFallback : error}
        </p>
        <button
          className="btn-primary mt-7 w-full"
          onClick={() => (canRetryHere ? setAttempt((a) => a + 1) : onRetake())}
        >
          {COPY.processing.tryAgain}
        </button>
        <button className="btn-quiet mt-2 w-full" onClick={onRestart}>
          {COPY.processing.startOver}
        </button>
      </div>
    );
  }

  const { phase, complete } = status ? statusToPhaseView(status.status) : { phase: "uploading" as const, complete: false };

  return (
    <div className="animate-fade-up flex w-full max-w-sm flex-col items-center">
      <ProgressRing progress={phaseProgress(phase, complete)} spinning>
        <div className="px-6">
          <div className="text-base font-medium uppercase tracking-[0.12em] text-accent">
            {COPY.processing.working}
          </div>
          <div className="mt-1.5 text-2xl font-semibold leading-tight">{PHASE_COPY[phase].label}</div>
          <div className="mt-1 text-base leading-tight text-muted">{PHASE_COPY[phase].caption}</div>
        </div>
      </ProgressRing>

      <div className="card mt-10 w-full rounded-2xl px-5 py-4">
        <PhaseSteps current={phase} complete={complete} />
      </div>

      <p className="mt-6 text-center text-base text-faint">
        {resumed ? COPY.processing.resumed + " " : ""}
        {COPY.processing.keepOpen}
      </p>
    </div>
  );
}
