import { LogoMark } from "../components/Logo";
import { ArrowRightIcon, RulerIcon, ShieldIcon, SparkIcon } from "../components/icons";
import { COPY } from "../lib/copy";

export function Welcome({ onStart }: { onStart: () => void }) {
  const c = COPY.welcome;
  return (
    <div className="animate-fade-up flex w-full flex-col items-center text-center">
      {/* hero mark with a soft glow */}
      <div className="relative mb-8 mt-4 grid place-items-center">
        <div
          className="absolute h-44 w-44 rounded-full blur-2xl"
          style={{ background: "radial-gradient(circle, rgba(45,212,191,0.35), transparent 70%)" }}
        />
        <div className="relative grid h-28 w-28 place-items-center rounded-3xl border border-line bg-surface/60 shadow-card backdrop-blur-xl">
          <LogoMark size={62} />
        </div>
      </div>

      <p className="eyebrow mb-3">{c.eyebrow}</p>
      <h1 className="text-balance text-[2rem] font-semibold leading-[1.1] tracking-tight sm:text-[2.4rem]">
        {c.headlineA} <span className="text-gradient">{c.headlineB}</span>
      </h1>
      <p className="mt-4 max-w-sm text-pretty text-lg leading-relaxed text-muted">{c.body}</p>

      <div className="mt-9 w-full max-w-sm">
        <button className="btn-primary w-full" onClick={onStart}>
          {c.start}
          <ArrowRightIcon className="h-5 w-5" />
        </button>
        <p className="mt-3 text-base text-faint">{c.duration}</p>
      </div>

      <div className="mt-10 grid w-full max-w-sm grid-cols-3 gap-2.5">
        <Feature icon={<RulerIcon className="h-5 w-5" />} {...c.features.accurate} />
        <Feature icon={<SparkIcon className="h-5 w-5" />} {...c.features.anyPhone} />
        <Feature icon={<ShieldIcon className="h-5 w-5" />} {...c.features.private} />
      </div>
    </div>
  );
}

function Feature({ icon, label, sub }: { icon: React.ReactNode; label: string; sub: string }) {
  return (
    <div className="card flex flex-col items-center gap-1.5 rounded-2xl px-2 py-4">
      <div className="text-accent">{icon}</div>
      <div className="text-base font-semibold leading-none">{label}</div>
      <div className="text-base leading-none text-faint">{sub}</div>
    </div>
  );
}
