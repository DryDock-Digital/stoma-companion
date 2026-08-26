export function LogoMark({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-hidden>
      <defs>
        <linearGradient id="lg" x1="8" y1="8" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop stopColor="#5EEAD4" />
          <stop offset="1" stopColor="#22D3EE" />
        </linearGradient>
      </defs>
      <circle cx="24" cy="24" r="21" stroke="url(#lg)" strokeWidth="2.5" opacity="0.35" />
      <circle cx="24" cy="24" r="13.5" stroke="url(#lg)" strokeWidth="3" />
      <circle cx="24" cy="24" r="5.5" fill="url(#lg)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-3">
      <LogoMark size={34} />
      <div className="text-base font-semibold leading-none tracking-tight">
        Stoma<span className="text-muted"> Companion</span>
      </div>
    </div>
  );
}
