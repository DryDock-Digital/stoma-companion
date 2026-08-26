# web — patient app (P3)

One web codebase, wrapped with Capacitor for iOS + Android (decisions.md D2). Three
screens, zero parameters exposed, built for patients (48% over 60): large targets,
one action per screen, plain language (FR-13, NFR-05). Talks only to the backend API.

## Stack

Vite + React + TypeScript + Tailwind. Dark, high-contrast design system in
`tailwind.config.js` + `src/index.css` (tokens: `base`/`surface`/`accent`, big
rounded controls, soft glows). No UI framework — a handful of hand-built components.

```
src/
  App.tsx              flow controller (welcome → capture → processing → result)
  api/client.ts        ScanApi (upload/poll): RealScanApi + SimulatedScanApi; retry, poll ceiling
  lib/flow.ts          JobStatus → patient phase mapping (FR-12)
  lib/copy.ts          every patient-facing string (one file to review for language)
  lib/storage.ts       scan-id persistence (resume polling after phone lock)
  lib/sample.ts        sample measurement for the demo/simulated path
  components/          Logo, ProgressRing, PhaseSteps, OutlineChart, icons
  screens/             Welcome, Capture (MediaRecorder), Processing, Result
```

## Run

```bash
npm install
npm run dev        # http://localhost:5173  (demo mode — no backend needed)
npm run build      # typecheck + production build → dist/
```

### Demo vs real backend

With **no** `VITE_API_BASE` set the app runs a fully simulated flow (fake progress +
a sample result) so it's demoable anywhere. Point it at the service to run real
scans:

```bash
echo 'VITE_API_BASE=https://159-65-233-200.sslip.io' > .env   # must be https (mixed content)
```

(The backend measurement result is wired in P1-10; until then a real scan progresses
through reconstruction and the result view fills from the API when it lands.)

## Capacitor shells

```bash
npm run build
npx cap add ios          # or: npx cap add android
npm run cap:sync
```

`capacitor.config.ts` sets the app id/name and `webDir: dist`. Camera capture uses the
web `MediaRecorder` inside the shell (the P0-6 spike decides whether the native camera
plugin is needed on any device).

## Device matrix (P3-6)

Verify the same build in a desktop browser, an iPhone (Capacitor), and an Android
phone against one API. Deferred to a real-device pass.
