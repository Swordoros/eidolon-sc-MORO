"use client";

// The songs the signed-in user has starred, kept on the server so every device sees the
// same list. The first component that needs it loads it once; a star is shown at once and
// rolled back if the server refuses it.

import { useEffect, useSyncExternalStore } from "react";

import { musicFavorites, starSong, unstarSong } from "@/lib/music";

export type FavoritesSnapshot = {
  userId: string | null;
  ids: ReadonlySet<string>;
  loaded: boolean;
  loading: boolean;
  failed: boolean;
};

const EMPTY: ReadonlySet<string> = new Set();
const SERVER: FavoritesSnapshot = {
  userId: null,
  ids: EMPTY,
  loaded: false,
  loading: false,
  failed: false,
};

let current: FavoritesSnapshot = SERVER;
const listeners = new Set<() => void>();
let loadingFor: string | null = null;

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeFavorites(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function favoritesSnapshot(): FavoritesSnapshot {
  return current;
}

/** Load the user's stars once, or again when `force` is true. */
export async function ensureFavorites(userId: string, force = false): Promise<void> {
  if (!userId) return;
  if (!force && current.userId === userId && (current.loaded || loadingFor === userId)) return;
  loadingFor = userId;
  current = { ...current, userId, loading: true, failed: false };
  emit();
  try {
    const { favorites } = await musicFavorites();
    if (current.userId !== userId) return;
    current = {
      userId,
      ids: new Set(favorites.map((favorite) => favorite.song_id)),
      loaded: true,
      loading: false,
      failed: false,
    };
  } catch {
    if (current.userId !== userId) return;
    current = { ...current, loading: false, failed: true };
  } finally {
    if (loadingFor === userId) loadingFor = null;
    emit();
  }
}

/** Star or unstar `songId`, showing it at once and undoing it if the server refuses. */
export async function toggleFavorite(userId: string, songId: string): Promise<void> {
  if (current.userId !== userId) await ensureFavorites(userId);
  if (current.userId !== userId) return;
  const starred = current.ids.has(songId);
  const next = new Set(current.ids);
  if (starred) next.delete(songId);
  else next.add(songId);
  current = { ...current, ids: next, failed: false };
  emit();
  try {
    if (starred) await unstarSong(songId);
    else await starSong(songId);
  } catch (error) {
    const reverted = new Set(current.ids);
    if (starred) reverted.add(songId);
    else reverted.delete(songId);
    current = { ...current, ids: reverted, failed: true };
    emit();
    throw error;
  }
}

export function useMusicFavorites(userId: string | null) {
  const snapshot = useSyncExternalStore(
    subscribeFavorites,
    favoritesSnapshot,
    () => SERVER,
  );

  useEffect(() => {
    if (userId) void ensureFavorites(userId);
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    const refresh = () => void ensureFavorites(userId, true);
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [userId]);

  return {
    ...snapshot,
    toggle: (songId: string) => userId ? toggleFavorite(userId, songId) : Promise.resolve(),
  };
}
