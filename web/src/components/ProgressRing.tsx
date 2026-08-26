import type { ReactNode } from "react";

/** Animated circular progress with a gradient arc. Children render in the centre. */
export function ProgressRing({
  progress,
  size = 232,
  stroke = 12,
  children,
  spinning = false,
}: {
  progress: number; // 0..1
  size?: number;
  stroke?: number;
  children?: ReactNode;
  spinning?: boolean;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, progress));

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#5EEAD4" />
            <stop offset="100%" stopColor="#22D3EE" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(255,255,255,0.06)" strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke="url(#ring)"
          strokeWidth={stroke}
          strokeLinecap="round"
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - clamped)}
          style={{
            transition: "stroke-dashoffset 0.8s cubic-bezier(0.22,1,0.36,1)",
            filter: "drop-shadow(0 0 10px rgba(45,212,191,0.5))",
          }}
        />
      </svg>
      {spinning && (
        <div
          className="absolute inset-0 rounded-full"
          style={{
            background:
              "conic-gradient(from 0deg, transparent 0deg, rgba(94,234,212,0.16) 60deg, transparent 120deg)",
            animation: "spin 2.6s linear infinite",
            mask: `radial-gradient(farthest-side, transparent calc(100% - ${stroke + 2}px), #000 0)`,
            WebkitMask: `radial-gradient(farthest-side, transparent calc(100% - ${stroke + 2}px), #000 0)`,
          }}
        />
      )}
      <div className="absolute inset-0 grid place-items-center text-center">{children}</div>
    </div>
  );
}
