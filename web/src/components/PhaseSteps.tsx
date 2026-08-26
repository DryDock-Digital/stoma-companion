import { PHASE_COPY, PHASES, type Phase } from "../lib/flow";
import { CheckIcon } from "./icons";

export function PhaseSteps({ current }: { current: Phase }) {
  const activeIndex = PHASES.indexOf(current);

  return (
    <ol className="flex flex-col gap-1">
      {PHASES.map((phase, i) => {
        const done = i < activeIndex;
        const active = i === activeIndex;
        return (
          <li key={phase} className="flex items-center gap-3.5 py-1.5">
            <div className="relative grid h-8 w-8 shrink-0 place-items-center">
              {i < PHASES.length - 1 && (
                <span
                  className="absolute left-1/2 top-8 h-[calc(100%-4px)] w-px -translate-x-1/2"
                  style={{ background: done ? "rgba(45,212,191,0.5)" : "rgba(255,255,255,0.08)" }}
                />
              )}
              <span
                className={
                  "grid h-8 w-8 place-items-center rounded-full border transition-colors " +
                  (done
                    ? "border-accent/50 bg-accent/15 text-accent"
                    : active
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-line bg-white/[0.02] text-faint")
                }
              >
                {done ? (
                  <CheckIcon className="h-4 w-4" />
                ) : active ? (
                  <span className="h-2.5 w-2.5 rounded-full bg-accent animate-breathe" />
                ) : (
                  <span className="h-2 w-2 rounded-full bg-current" />
                )}
              </span>
            </div>
            <div className="min-w-0">
              <div
                className={
                  "text-[15px] font-medium leading-tight " +
                  (active ? "text-ink" : done ? "text-muted" : "text-faint")
                }
              >
                {PHASE_COPY[phase].label}
              </div>
              {active && (
                <div className="text-[13px] leading-tight text-muted">{PHASE_COPY[phase].caption}</div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
