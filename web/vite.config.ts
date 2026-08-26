import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served from a subpath on GitHub Pages (/stoma-companion/); "/" for local dev.
// The Pages workflow sets PAGES_BASE.
export default defineConfig({
  base: process.env.PAGES_BASE || "/",
  plugins: [react()],
  server: { port: 5173, host: true },
});
