import type { CapacitorConfig } from "@capacitor/cli";

// iOS/Android shells wrap the same web build (decisions.md D2). Native projects are
// added with `npx cap add ios` / `npx cap add android` and synced via `npm run cap:sync`.
const config: CapacitorConfig = {
  appId: "digital.drydock.stoma",
  appName: "Stoma Companion",
  webDir: "dist",
  backgroundColor: "#08090C",
  ios: { contentInset: "always" },
  server: { androidScheme: "https" },
};

export default config;
