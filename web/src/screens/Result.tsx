import type { ScanStatus } from "../api/client";
import { OutlineChart } from "../components/OutlineChart";
import { CheckIcon } from "../components/icons";

export function Result({ status, onRestart }: { status: ScanStatus; onRestart: () => void }) {
  const result = status.result ?? undefined;
  const diameter = result?.diameter_mm;
  const tol = result?.tolerance_mm ?? 1;
  const within = result?.within_tolerance ?? true;
  const dev = result?.deviation_mm;

  return (
    <div className="animate-fade-up flex w-full max-w-sm flex-col items-center">
      <div className="mb-1 grid h-14 w-14 animate-scale-in place-items-center rounded-full border border-accent/40 bg-accent/12 text-accent">
        <CheckIcon className="h-7 w-7" />
      </div>
      <p className="eyebrow mt-3">Your wafer is ready</p>

      {/* headline measurement */}
      <div className="mt-2 flex items-end gap-1.5">
        <span className="text-6xl font-semibold tracking-tight tabular-nums text-gradient">
          {diameter != null ? diameter.toFixed(1) : "—"}
        </span>
        <span className="mb-2 text-xl font-medium text-muted">mm</span>
      </div>
      <p className="text-[14px] text-muted">Base diameter</p>

      {within && diameter != null && (
        <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-3 py-1 text-[13px] font-medium text-success">
          <CheckIcon className="h-3.5 w-3.5" />
          Within ±{tol} mm{dev != null ? ` · ${dev.toFixed(1)} mm off target` : ""}
        </div>
      )}

      {/* outline + wafer overlay */}
      {result?.outline_mm && (
        <div className="card mt-7 w-full overflow-hidden rounded-3xl p-3">
          <div className="aspect-square w-full">
            <OutlineChart result={result} />
          </div>
          <div className="flex items-center justify-center gap-5 border-t border-line px-2 pb-1 pt-3 text-[12px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-accent" /> Stoma base
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0 w-4 border-t-2 border-dashed border-muted" /> Wafer cut
            </span>
          </div>
        </div>
      )}

      <div className="mt-8 w-full">
        <button className="btn-primary w-full" onClick={onRestart}>
          Start a new scan
        </button>
        {result?.engine && (
          <p className="mt-3 text-center text-[11px] text-faint">
            Reconstructed with {result.engine}
          </p>
        )}
      </div>
    </div>
  );
}
