/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Near-black base with a faint cool cast — calm, medical, modern.
        base: "#08090C",
        elev: "#0E1116",
        surface: "#141920",
        "surface-2": "#1B212A",
        line: "rgba(255,255,255,0.07)",
        "line-strong": "rgba(255,255,255,0.13)",
        ink: "#F3F6F9",
        muted: "#9AA5B2",
        faint: "#5B6572",
        accent: "#2DD4BF",
        "accent-strong": "#14B8A6",
        "accent-ink": "#04211D",
        cyan: "#22D3EE",
        success: "#34D399",
        warn: "#FBBF24",
        danger: "#F87171",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Inter",
          "Segoe UI",
          "Roboto",
          "system-ui",
          "sans-serif",
        ],
      },
      borderRadius: { xl: "1rem", "2xl": "1.5rem", "3xl": "2rem" },
      boxShadow: {
        glow: "0 0 0 1px rgba(45,212,191,0.35), 0 12px 40px -12px rgba(45,212,191,0.45)",
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 24px 60px -30px rgba(0,0,0,0.8)",
        lift: "0 18px 50px -20px rgba(0,0,0,0.75)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.96)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        breathe: {
          "0%,100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s cubic-bezier(0.22,1,0.36,1) both",
        "scale-in": "scale-in 0.4s cubic-bezier(0.22,1,0.36,1) both",
        breathe: "breathe 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
