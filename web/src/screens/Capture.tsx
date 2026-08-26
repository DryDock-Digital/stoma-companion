import { useEffect, useRef, useState } from "react";
import { isSimulated } from "../api/client";
import { CameraIcon } from "../components/icons";
import { COPY } from "../lib/copy";

const TARGET_SECONDS = 15;
const MIN_SECONDS = 3;
/** Hard stop guard — fires even if the interval tick is throttled (backgrounded tab). */
const HARD_STOP_MS = (TARGET_SECONDS + 2) * 1000;

type Mode = "init" | "ready" | "recording" | "finishing" | "error";

function pickMimeType(): string | undefined {
  const candidates = ["video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return candidates.find((t) => "MediaRecorder" in window && MediaRecorder.isTypeSupported(t));
}

function cameraErrorCopy(err: unknown): string {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return COPY.capture.errors.denied;
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return COPY.capture.errors.notFound;
    case "NotReadableError":
    case "TrackStartError":
      return COPY.capture.errors.inUse;
    default:
      return COPY.capture.errors.generic;
  }
}

export function Capture({
  onCaptured,
  onSample,
}: {
  onCaptured: (video: Blob) => void;
  /** Demo-only escape hatch; never offered when a real backend is configured. */
  onSample: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const hardStopRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const [mode, setMode] = useState<Mode>("init");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [cameraAttempt, setCameraAttempt] = useState(0);
  const allowSample = isSimulated();

  function fail(message: string) {
    clearHardStop();
    setError(message);
    setMode("error");
  }

  function clearHardStop() {
    if (hardStopRef.current != null) {
      window.clearTimeout(hardStopRef.current);
      hardStopRef.current = null;
    }
  }

  // Open the camera (re-runs on retry).
  useEffect(() => {
    let cancelled = false;
    setError(null);
    setMode("init");
    (async () => {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        if (!cancelled) fail(COPY.capture.errors.insecure);
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
        setMode("ready");
      } catch (err) {
        if (!cancelled) fail(cameraErrorCopy(err));
      }
    })();
    return () => {
      cancelled = true;
      clearHardStop();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraAttempt]);

  // Elapsed ticker + interval-based auto-stop.
  useEffect(() => {
    if (mode !== "recording") return;
    const id = setInterval(() => {
      const secs = (Date.now() - startedAtRef.current) / 1000;
      setElapsed(secs);
      if (secs >= TARGET_SECONDS) stop();
    }, 100);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  function start() {
    const stream = streamRef.current;
    if (!stream) return fail(COPY.capture.errors.generic);
    try {
      const mimeType = pickMimeType();
      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onerror = () => fail(COPY.capture.errors.recorder);
      rec.onstop = () => {
        clearHardStop();
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "video/webm" });
        chunksRef.current = [];
        streamRef.current?.getTracks().forEach((t) => t.stop());
        const secs = (Date.now() - startedAtRef.current) / 1000;
        if (blob.size === 0) return fail(COPY.capture.errors.recorder);
        if (secs < MIN_SECONDS) return fail(COPY.capture.errors.tooShort);
        onCaptured(blob);
      };
      recorderRef.current = rec;
      rec.start(1000); // timeslice: chunks flush every second, nothing lost on a crash
      startedAtRef.current = Date.now();
      setElapsed(0);
      setMode("recording");
      hardStopRef.current = window.setTimeout(stop, HARD_STOP_MS);
    } catch {
      fail(COPY.capture.errors.recorder);
    }
  }

  function stop() {
    const rec = recorderRef.current;
    if (!rec) return;
    if (rec.state === "recording" || rec.state === "paused") {
      setMode("finishing");
      try {
        rec.stop();
      } catch {
        fail(COPY.capture.errors.recorder);
      }
    }
  }

  const progress = Math.min(1, elapsed / TARGET_SECONDS);

  if (mode === "error") {
    return (
      <div className="animate-fade-up flex w-full max-w-sm flex-col items-center text-center">
        <div className="mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-line bg-surface/60 text-muted">
          <CameraIcon className="h-7 w-7" />
        </div>
        <h2 className="text-2xl font-semibold">{COPY.capture.errors.title}</h2>
        <p className="mt-3 text-lg leading-relaxed text-muted">{error}</p>
        <button className="btn-primary mt-7 w-full" onClick={() => setCameraAttempt((a) => a + 1)}>
          {COPY.capture.retry}
        </button>
        {allowSample && (
          <button className="btn-quiet mt-2 w-full" onClick={onSample}>
            {COPY.capture.sample}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="animate-fade-up flex w-full max-w-sm flex-col items-center">
      {/* live camera framed like a viewfinder */}
      <div className="relative aspect-[3/4] w-full overflow-hidden rounded-3xl border border-line-strong bg-black shadow-lift">
        <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-cover" />

        {/* top scrim + guidance */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-black/70 to-transparent" />
        <div className="absolute inset-x-0 top-0 flex justify-center p-4">
          <div className="pill border-white/15 bg-black/40 text-center text-base text-ink backdrop-blur">
            {mode === "recording" ? COPY.capture.guideRecording : COPY.capture.guideReady}
          </div>
        </div>

        {/* framing reticle */}
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <div
            className={
              "h-52 w-52 rounded-full border-2 transition-colors " +
              (mode === "recording" ? "border-accent/80" : "border-white/50")
            }
            style={{ boxShadow: "0 0 0 9999px rgba(0,0,0,0.28)" }}
          />
        </div>

        {/* recording indicator */}
        {mode === "recording" && (
          <div className="absolute left-4 top-16 flex items-center gap-2 rounded-full bg-black/45 px-3 py-1 backdrop-blur">
            <span className="h-2.5 w-2.5 rounded-full bg-danger animate-breathe" />
            <span className="text-base font-medium tabular-nums">
              {Math.ceil(Math.max(0, TARGET_SECONDS - elapsed))}s
            </span>
          </div>
        )}
      </div>

      {mode !== "recording" && (
        <ol className="mt-5 w-full space-y-1.5 text-base leading-snug text-muted">
          {COPY.capture.tips.map((tip, i) => (
            <li key={i} className="flex gap-2.5">
              <span className="font-semibold text-accent">{i + 1}.</span>
              <span>{tip}</span>
            </li>
          ))}
        </ol>
      )}

      {/* shutter */}
      <div className="mt-6 flex flex-col items-center">
        <button
          onClick={mode === "recording" ? stop : start}
          disabled={mode === "init" || mode === "finishing"}
          aria-label={mode === "recording" ? "Stop recording" : "Start recording"}
          className="relative grid h-20 w-20 place-items-center rounded-full transition-transform active:scale-95 disabled:opacity-50"
        >
          <svg className="absolute -rotate-90" width="80" height="80">
            <circle cx="40" cy="40" r="36" stroke="rgba(255,255,255,0.15)" strokeWidth="4" fill="none" />
            <circle
              cx="40"
              cy="40"
              r="36"
              stroke="#2DD4BF"
              strokeWidth="4"
              fill="none"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 36}
              strokeDashoffset={2 * Math.PI * 36 * (1 - progress)}
              style={{ transition: "stroke-dashoffset 0.15s linear" }}
            />
          </svg>
          <span
            className={
              "transition-all " +
              (mode === "recording"
                ? "h-7 w-7 rounded-md bg-danger"
                : "h-16 w-16 rounded-full bg-white shadow-[0_0_0_4px_rgba(0,0,0,0.4)]")
            }
          />
        </button>
        <p className="mt-4 text-base text-muted">
          {mode === "recording" ? COPY.capture.tapToFinish : COPY.capture.tapToRecord}
        </p>
      </div>
    </div>
  );
}
