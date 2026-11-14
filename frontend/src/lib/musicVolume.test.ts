import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_VOLUME, loadMusicVolume, saveMusicVolume } from "./musicVolume";

afterEach(() => {
  vi.unstubAllGlobals();
});

function storage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
  });
  return items;
}

describe("music volume", () => {
  it("is full until this browser saves another value", () => {
    storage();
    expect(loadMusicVolume()).toBe(DEFAULT_VOLUME);

    saveMusicVolume(0.35);

    expect(loadMusicVolume()).toBe(0.35);
  });

  it("clamps saved values and ignores broken ones", () => {
    storage({ "eidolon.musicVolume": "2" });
    expect(loadMusicVolume()).toBe(1);

    storage({ "eidolon.musicVolume": "not a number" });
    expect(loadMusicVolume()).toBe(DEFAULT_VOLUME);
  });

  it("does not throw when storage is unavailable", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("denied"); },
      setItem: () => { throw new Error("denied"); },
    });

    expect(loadMusicVolume()).toBe(DEFAULT_VOLUME);
    expect(() => saveMusicVolume(0.5)).not.toThrow();
  });
});
