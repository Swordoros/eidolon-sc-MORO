// The player's volume, kept on this device: one preference for this browser, not an
// account setting. It survives reloads and does not change on another device.

const KEY = "eidolon.musicVolume";
export const DEFAULT_VOLUME = 1;

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** This browser's volume, 0 to 1; full when nothing valid was kept. */
export function loadMusicVolume(): number {
  try {
    const raw = localStorage.getItem(KEY);
    const stored = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(stored) ? clamp(stored) : DEFAULT_VOLUME;
  } catch {
    return DEFAULT_VOLUME;
  }
}

export function saveMusicVolume(volume: number): void {
  try {
    localStorage.setItem(KEY, String(clamp(volume)));
  } catch {
    // Storage unavailable: the slider still holds for this page.
  }
}
