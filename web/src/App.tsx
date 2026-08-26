import { useEffect, useState } from "react";
import type { ScanStatus } from "./api/client";
import { Wordmark } from "./components/Logo";
import { COPY } from "./lib/copy";
import { isSuccess } from "./lib/flow";
import { clearScanId, loadScanId } from "./lib/storage";
import { Capture } from "./screens/Capture";
import { Processing } from "./screens/Processing";
import { Result } from "./screens/Result";
import { Welcome } from "./screens/Welcome";
import { Admin } from "./screens/Admin";

// Engineer test bench lives at /admin. The patient flow never links to it.
const IS_ADMIN = window.location.pathname.startsWith("/admin");

type Step = "welcome" | "capture" | "processing" | "result";

export function App() {
  if (IS_ADMIN) return <Admin />;
  return <PatientApp />;
}

function PatientApp() {
  const [step, setStep] = useState<Step>("welcome");
  const [video, setVideo] = useState<Blob | null>(null);
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [live, setLive] = useState<ScanStatus | null>(null);
  const [final, setFinal] = useState<ScanStatus | null>(null);

  // Phone locked mid-job? Pick the job back up instead of starting over.
  useEffect(() => {
    const stored = loadScanId();
    if (stored) {
      setResumeId(stored);
      setStep("processing");
    }
  }, []);

  const restart = () => {
    clearScanId();
    setVideo(null);
    setResumeId(null);
    setLive(null);
    setFinal(null);
    setStep("welcome");
  };

  const retake = () => {
    clearScanId();
    setVideo(null);
    setResumeId(null);
    setLive(null);
    setFinal(null);
    setStep("capture");
  };

  // "Demo mode" is driven by the run itself, never by config alone.
  const simulated = (final ?? live)?.simulated === true;

  return (
    <div className="safe mx-auto flex min-h-[100dvh] w-full max-w-md flex-col">
      <header className="flex h-12 items-center justify-between">
        {step === "capture" ? (
          <button
            className="-ml-3 inline-flex min-h-12 min-w-12 items-center gap-1 px-3 text-base text-muted transition-colors hover:text-ink"
            onClick={restart}
          >
            <span aria-hidden>←</span> {COPY.app.back}
          </button>
        ) : (
          <Wordmark />
        )}
        {simulated && <span className="pill border-accent/25 text-accent">{COPY.app.demoPill}</span>}
      </header>

      <main className="flex flex-1 items-center justify-center py-6">
        {step === "welcome" && <Welcome onStart={() => setStep("capture")} />}

        {step === "capture" && (
          <Capture
            onCaptured={(blob) => {
              setVideo(blob);
              setResumeId(null);
              setStep("processing");
            }}
            onSample={() => {
              setVideo(null);
              setResumeId(null);
              setStep("processing");
            }}
          />
        )}

        {step === "processing" && (
          <Processing
            video={video}
            resumeId={resumeId}
            onStatus={setLive}
            onComplete={(status) => {
              setVideo(null); // release the recording once the job is complete
              setResumeId(null);
              if (!isSuccess(status.status)) return; // failed jobs never reach Result
              setFinal(status);
              setStep("result");
            }}
            onRetake={retake}
            onRestart={restart}
          />
        )}

        {step === "result" && final && isSuccess(final.status) && (
          <Result status={final} onRestart={restart} onRescan={retake} />
        )}
      </main>
    </div>
  );
}
