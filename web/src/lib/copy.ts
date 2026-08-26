// Every patient-facing string lives here so there is one file to review for
// language (FR-13, NFR-05: plain words, no "marker", "3D", "mesh", "reconstruct";
// 48% of patients are over 60).

import type { Phase } from "./flow";

export const COPY = {
  app: {
    back: "Back",
    demoPill: "Demo mode",
  },

  welcome: {
    eyebrow: "Measure · Cut · Fit",
    headlineA: "A wafer that fits,",
    headlineB: "from a short video.",
    body:
      "Place the printed square card flat on your skin next to your stoma, then record a short video with your phone. We measure the base and shape the wafer for you — no measuring, no guesswork.",
    start: "Start scan",
    duration: "Takes about two minutes.",
    features: {
      accurate: { label: "±1 mm", sub: "accurate" },
      anyPhone: { label: "Any phone", sub: "no add-ons" },
      private: { label: "Private", sub: "your scan" },
    },
  },

  capture: {
    guideReady: "Keep the square card and your stoma in view",
    guideRecording: "Move the phone slowly around your stoma",
    tapToRecord: "Tap to record",
    tapToFinish: "Tap to finish",
    tips: [
      "Place the printed square card flat on the skin, next to your stoma.",
      "Keep both the card and your stoma in the picture the whole time.",
      "Move the phone slowly in a circle around your stoma.",
    ],
    retry: "Try again",
    sample: "Continue with a sample",
    errors: {
      title: "Camera not available",
      denied: "Camera access is off. Allow it in your settings and try again.",
      notFound: "No camera was found on this device.",
      insecure: "This page needs a secure (https) connection to use the camera.",
      inUse: "The camera is being used by another app. Close it and try again.",
      generic: "We couldn't start the camera. Please try again.",
      recorder: "We couldn't record the video. Please try again.",
      tooShort: "That recording was too short. Please try again.",
    },
  },

  phases: {
    uploading: { label: "Uploading", caption: "Sending your video securely" },
    reconstructing: { label: "Preparing", caption: "Getting your video ready" },
    measuring: { label: "Measuring", caption: "Working on your measurement" },
    cutting: { label: "Cutting", caption: "Shaping your wafer to fit" },
    done: { label: "Ready", caption: "Your wafer is ready" },
    error: { label: "We hit a snag", caption: "Let's try that again" },
  } satisfies Record<Phase, { label: string; caption: string }>,

  processing: {
    working: "Working",
    next: "Next",
    keepOpen: "Keep the app open — this usually takes under two minutes.",
    resumed: "Picking up where we left off.",
    tryAgain: "Try again",
    startOver: "Start over",
    failedTitle: "We couldn't finish this scan",
    failedFallback: "Something went wrong with this scan. Please try again.",
  },

  errors: {
    network: "We couldn't reach the service. Check your connection and try again.",
    lostConnection: "Lost connection. Please check your network and try again.",
    timeout: "This is taking longer than expected. Please try again.",
    tooLarge: "That video is too long. Please record a shorter one.",
    badVideo: "We couldn't use that video. Please record it again.",
    server: "The service is having trouble right now. Please try again in a moment.",
    noVideo: "We didn't get a video. Please record it again.",
    generic: "Something went wrong. Please try again.",
  },

  result: {
    headline: {
      measured: "Your measurement is ready",
      measuredSub: "Your wafer will be cut next.",
      done: "Your wafer is ready",
      cutQueued: "Your wafer is next in line",
      cutQueuedSub: "The cutter will start shortly.",
      cutting: "Your wafer is being cut",
      cuttingSub: "This only takes a moment.",
      cutDone: "Your wafer is ready",
      cutFailed: "We couldn't cut your wafer",
      cutFailedSub: "Please ask for help or scan again.",
    },
    diameterLabel: "Base width",
    withinTolerance: (tolMm: number) => `Within ±${tolMm} mm`,
    rescanTitle: "Let's scan once more",
    rescanBody: "We couldn't get a clear enough measurement this time. Please record the video again.",
    legendBase: "Stoma base",
    legendWafer: "Wafer cut line",
    scaleBar: "10 mm",
    newScan: "Start a new scan",
    scanAgain: "Scan again",
  },
} as const;
