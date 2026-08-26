// localStorage is best-effort only (private mode, Capacitor quirks, quota) — every
// access is wrapped so a storage failure can never break the patient flow.

const SCAN_KEY = "stoma.currentScanId";

export function loadScanId(): string | null {
  try {
    return window.localStorage.getItem(SCAN_KEY);
  } catch {
    return null;
  }
}

export function saveScanId(id: string): void {
  try {
    window.localStorage.setItem(SCAN_KEY, id);
  } catch {
    /* ignore */
  }
}

export function clearScanId(): void {
  try {
    window.localStorage.removeItem(SCAN_KEY);
  } catch {
    /* ignore */
  }
}
