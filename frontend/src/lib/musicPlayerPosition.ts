// Where the floating player was last put on this device, in viewport pixels. Null until
// the user drags it; then it stays there across page changes and reloads.

const KEY = "eidolon.musicPlayerPosition";

export type PlayerPosition = { x: number; y: number };

export function loadPlayerPosition(): PlayerPosition | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const stored = JSON.parse(raw) as Partial<PlayerPosition>;
    if (!Number.isFinite(stored.x) || !Number.isFinite(stored.y)) return null;
    return { x: Number(stored.x), y: Number(stored.y) };
  } catch {
    return null;
  }
}

export function savePlayerPosition(position: PlayerPosition): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(position));
  } catch {
    // Storage unavailable: it still stays where it was put until this page closes.
  }
}
