import { afterEach, describe, expect, it, vi } from "vitest";

import { ensureFavorites, favoritesSnapshot, toggleFavorite } from "./musicFavorites";

afterEach(() => {
  vi.unstubAllGlobals();
});

function calls(): string[] {
  const seen: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    seen.push(`${init?.method ?? "GET"} ${url}`);
    if (url.endsWith("/api/music/favorites")) {
      return Response.json({ favorites: [{ song_id: "song-1", created_at: "2026-11-14T15:00:00Z" }] });
    }
    return new Response(null, { status: 204 });
  }));
  return seen;
}

describe("music favorites", () => {
  it("loads the stars on the server for this account", async () => {
    calls();

    await ensureFavorites("favorites-user-1");

    expect(favoritesSnapshot()).toMatchObject({
      userId: "favorites-user-1",
      loaded: true,
      failed: false,
    });
    expect([...favoritesSnapshot().ids]).toEqual(["song-1"]);
  });

  it("shows a toggle at once and sends it to the server", async () => {
    const seen = calls();
    await ensureFavorites("favorites-user-2");

    await toggleFavorite("favorites-user-2", "song-2");
    expect(favoritesSnapshot().ids.has("song-2")).toBe(true);
    expect(seen).toContain("PUT /api/music/favorites/song-2");

    await toggleFavorite("favorites-user-2", "song-2");
    expect(favoritesSnapshot().ids.has("song-2")).toBe(false);
    expect(seen).toContain("DELETE /api/music/favorites/song-2");
  });

  it("rolls back a star the server refuses", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/music/favorites")) {
        return Response.json({ favorites: [] });
      }
      return Response.json({ detail: "no" }, { status: 500 });
    }));
    await ensureFavorites("favorites-user-3");

    await expect(toggleFavorite("favorites-user-3", "song-3")).rejects.toBeTruthy();

    expect(favoritesSnapshot().ids.has("song-3")).toBe(false);
    expect(favoritesSnapshot().failed).toBe(true);
  });
});
