import { useEffect, useRef, useState } from "react";
import { CameraIcon } from "../components/icons";

const TARGET_SECONDS = 15;

type Mode = "init" | "ready" | "recording" | "finishing" | "denied";

function pickMimeType(): string | undefined {
  const candidates = ["video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return candidates.find((t) => "MediaRecorder" in window && MediaRecorder.isTypeSupported(t));
}

export function Capture({
  onCaptured,
  onDemo,
}: {
  onCaptured: (video: Blob) => void;
  onDemo: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const [mode, setMode] = useState<Mode>("init");
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
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
      } catch {
        if (!cancelled) setMode("denied");
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    if (mode !== "recording") return;
    const started = Date.now();
    const id = setInterval(() => {
      const secs = (Date.now() - started) / 1000;
      setElapsed(secs);
      if (secs >= TARGET_SECONDS) stop();
    }, 100);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  function start() {
    const stream = streamRef.current;
    if (!stream) return onDemo();
    try {
      const mimeType = pickMimeType();
      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
      rec.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "video/webm" });
        streamRef.current?.getTracks().forEach((t) => t.stop());
        onCaptured(blob);
      };
      recorderRef.current = rec;
      rec.start();
      setElapsed(0);
      setMode("recording");
    } catch {
      // some environments grant the camera but can't encode — fall back gracefully
      onDemo();
    }
  }

  function stop() {
    if (recorderRef.current?.state === "recording") {
      setMode("finishing");
      recorderRef.current.stop();
    }
  }

  const progress = Math.min(1, elapsed / TARGET_SECONDS);

  if (mode === "denied") {
    return (
      <div className="animate-fade-up flex w-full max-w-sm flex-col items-center text-center">
        <div className="mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-line bg-surface/60 text-muted">
          <CameraIcon className="h-7 w-7" />
        </div>
        <h2 className="text-xl font-semibold">Camera not available</h2>
        <p className="mt-2 text-[15px] text-muted">
          Allow camera access in your browser settings to record, or continue with a sample scan.
        </p>
        <button className="btn-primary mt-7 w-full" onClick={onDemo}>
          Continue with a sample
        </button>
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
          <div className="pill border-white/15 bg-black/40 text-[13px] text-ink backdrop-blur">
            {mode === "recording" ? "Slowly circle the stoma" : "Point at the marker and stoma"}
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
            <span className="text-[13px] font-medium tabular-nums">
              {Math.ceil(TARGET_SECONDS - elapsed)}s
            </span>
          </div>
        )}
      </div>

      {/* shutter */}
      <div className="mt-8 flex flex-col items-center">
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
        <p className="mt-4 text-[14px] text-muted">
          {mode === "recording" ? "Tap to finish" : "Tap to record"}
        </p>
      </div>
    </div>
  );
}
