import type { SVGProps } from "react";

const base = (p: SVGProps<SVGSVGElement>) => ({
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  ...p,
});

export const CameraIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h1.2l.9-1.4A1.5 1.5 0 0 1 8.9 4h6.2a1.5 1.5 0 0 1 1.3.6L17.3 6h1.2A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" />
    <circle cx="12" cy="12.2" r="3.4" />
  </svg>
);

export const CheckIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="m5 12.5 4.2 4.2L19 7" />
  </svg>
);

export const ArrowRightIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export const RefreshIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M4 9a8 8 0 0 1 13.7-3.4L20 8M20 4v4h-4" />
    <path d="M20 15a8 8 0 0 1-13.7 3.4L4 16M4 20v-4h4" />
  </svg>
);

export const ShieldIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M12 3 5 6v5c0 4.4 3 7.9 7 9 4-1.1 7-4.6 7-9V6z" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

export const SparkIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
  </svg>
);

export const RulerIcon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}>
    <rect x="3" y="8" width="18" height="8" rx="1.6" />
    <path d="M7 8v3M11 8v4M15 8v3M19 8v4" />
  </svg>
);
