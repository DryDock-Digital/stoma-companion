import { useState } from "react";
import { isSimulated, type ScanStatus } from "./api/client";
import { Wordmark } from "./components/Logo";
import { Capture } from "./screens/Capture";
import { Processing } from "./screens/Processing";
import { Result } from "./screens/Result";
import { Welcome } from "./screens/Welcome";

type Step = "welcome" | "capture" | "processing" | "result";

export function App() {
  const [step, setStep] = useState<Step>("welcome");
  const [video, setVideo] = useState<Blob | null>(null);
  const [final, setFinal] = useState<ScanStatus | null>(null);

  const restart = () => {
    setVideo(null);
    setFinal(null);
    setStep("welcome");
  };

  return (
    <div className="safe mx-auto flex min-h-[100dvh] w-full max-w-md flex-col">
      <header className="flex h-12 items-center justify-between">
        {step === "capture" ? (
          <button className="text-[15px] text-muted transition-colors hover:text-ink" onClick={restart}>
            ← Back
          </button>
        ) : (
          <Wordmark />
        )}
        {isSimulated() && (
          <span className="pill border-accent/25 text-accent">Demo mode</span>
        )}
      </header>

      <main className="flex flex-1 items-center justify-center py-6">
        {step === "welcome" && <Welcome onStart={() => setStep("capture")} />}

        {step === "capture" && (
          <Capture
            onCaptured={(blob) => {
              setVideo(blob);
              setStep("processing");
            }}
            onDemo={() => {
              setVideo(null);
              setStep("processing");
            }}
          />
        )}

        {step === "processing" && (
          <Processing
            video={video}
            onComplete={(status) => {
              setFinal(status);
              setStep("result");
            }}
            onRestart={restart}
          />
        )}

        {step === "result" && final && <Result status={final} onRestart={restart} />}
      </main>
    </div>
  );
}
