"use client";

// The player's global state: what is playing, its queue, and the play mode. The page owns
// the song lists; the shell owns the one <audio> (MusicPlayer), so changing pages does not
// interrupt it.

import { musicLookup } from "@/lib/music";
import { setListening } from "@/lib/musicListening";

/** What happens when a song ends: play the next one, repeat this one, or pick one at random. */
export type PlayMode = "sequence" | "repeat-one" | "shuffle";

/** One song waiting in the player's queue; NetEase's id is looked up when it is needed. */
export type QueuedSong = {
  songId: string;
  title: string;
  singers: string[];
  neteaseId?: string;
  alternatives?: string[];
};

/** 现在在放哪一首；null 是没在放。
 *
 * `at` / `duration` 是给页面显示进度用的（秒，`duration` 未知时是 0）；
 * `unplayable` 是那条音频取不到（多半是网易云没版权）——放着也没声，页面要照实说。
 */
export type Playing = {
  /** The world setting's song id, for its star; empty when the caller has none. */
  songId: string;
  title: string;
  singers: string[];
  neteaseId: string;
  /** Other NetEase ids of the same song, tried if this one cannot be streamed. */
  alternatives: string[];
  at: number;
  duration: number;
  /** Paused by the user; the queue and position stay. */
  paused: boolean;
  unplayable: boolean;
} | null;

const MODE_KEY = "eidolon.musicPlayMode";
const MODES: PlayMode[] = ["sequence", "repeat-one", "shuffle"];

let current: Playing = null;
let queue: QueuedSong[] = [];
let queueIndex = -1;
const blocked = new Set<string>();
let mode: PlayMode = loadMode();
let seekRequest: { at: number; key: number } | null = null;
let seekKey = 0;
const listeners = new Set<() => void>();

function loadMode(): PlayMode {
  try {
    const stored = localStorage.getItem(MODE_KEY);
    return stored === "repeat-one" || stored === "shuffle" ? stored : "sequence";
  } catch {
    return "sequence";
  }
}

function keepMode(next: PlayMode): void {
  try {
    localStorage.setItem(MODE_KEY, next);
  } catch {
    // Storage unavailable: the choice still holds for this page.
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

/** The line the chat prompt gets while this song plays; singers after the title if any. */
function listeningText(title: string, singers: string[]): string {
  return singers.length > 0 ? `${title} · ${singers.join(" / ")}` : title;
}

async function start(index: number): Promise<boolean> {
  const item = queue[index];
  if (!item) return false;
  let neteaseId = item.neteaseId;
  let alternatives = item.alternatives ?? [];
  if (!neteaseId) {
    try {
      const hit = await musicLookup(item.title);
      if (!hit) {
        blocked.add(item.songId);
        return false;
      }
      neteaseId = hit.id;
      alternatives = hit.alternatives ?? [];
      item.neteaseId = neteaseId;
      item.alternatives = alternatives;
    } catch {
      blocked.add(item.songId);
      return false;
    }
  }
  queueIndex = index;
  seekRequest = null;
  current = {
    songId: item.songId,
    title: item.title,
    singers: item.singers,
    neteaseId,
    alternatives,
    at: 0,
    duration: 0,
    paused: false,
    unplayable: false,
  };
  setListening(listeningText(current.title, current.singers));
  emit();
  return true;
}

async function advance(step: 1 | -1): Promise<void> {
  if (queue.length === 0) return;
  let index = queueIndex;
  for (let tried = 0; tried < queue.length; tried++) {
    index = mode === "shuffle"
      ? randomIndex()
      : (index + step + queue.length) % queue.length;
    if (blocked.has(queue[index]?.songId ?? "")) continue;
    if (await start(index)) return;
  }
  musicPlayback.stop();
}

export const musicPlayback = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** 给 `useSyncExternalStore` 的取快照：手机上首屏（还没跑脚本）当作没在放。 */
  get(): Playing {
    return current;
  },
  /** How songs advance after one ends, or when the user asks for the next one. */
  mode(): PlayMode {
    return mode;
  },
  queueLength(): number {
    return queue.length;
  },
  /** 放这一首；`list` is the list it was picked from, so next/previous know where to go. */
  play(
    songId: string,
    title: string,
    neteaseId: string,
    alternatives: string[] = [],
    list?: QueuedSong[],
    index = 0,
    singers: string[] = [],
  ): void {
    if (list && list.length > 0) {
      queue = list;
      queueIndex = Math.max(0, Math.min(index, list.length - 1));
    } else {
      queue = [{ songId, title, singers, neteaseId, alternatives }];
      queueIndex = 0;
    }
    blocked.delete(songId);
    if (current?.neteaseId === neteaseId && !current.unplayable) return;
    seekRequest = null;
    current = {
      songId, title, singers, neteaseId, alternatives, at: 0, duration: 0, paused: false,
      unplayable: false,
    };
    setListening(listeningText(title, singers));
    emit();
  },
  /** Pause without losing the queue or the position. */
  pause(): void {
    if (current === null || current.paused) return;
    current = { ...current, paused: true };
    setListening(null);
    emit();
  },
  /** Resume the paused song. */
  resume(): void {
    if (current === null || !current.paused) return;
    current = { ...current, paused: false };
    setListening(listeningText(current.title, current.singers));
    emit();
  },
  togglePaused(): void {
    if (current?.paused) musicPlayback.resume();
    else musicPlayback.pause();
  },
  /** Play the next song, wrapping at the end; shuffle picks one at random. */
  async next(): Promise<void> {
    await advance(1);
  },
  /** Play the previous song, wrapping at the start; shuffle picks one at random too. */
  async previous(): Promise<void> {
    await advance(-1);
  },
  /** Cycle sequence → repeat one → shuffle, keeping the choice on this device. */
  cycleMode(): void {
    const next = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    mode = next;
    keepMode(next);
    emit();
  },
  /** 播放器报到哪里了。按秒去通知，免得一秒画几十次。 */
  tick(at: number, duration: number): void {
    if (current === null) return;
    const second = Math.floor(at);
    if (current.at === second && current.duration === Math.floor(duration)) return;
    current = { ...current, at: second, duration: Math.floor(duration) };
    emit();
  },
  /** Move the song to `seconds`; the audio element takes the request on its next render. */
  seek(seconds: number): void {
    if (current === null) return;
    const at = Math.max(0, Math.min(seconds, current.duration || seconds));
    current = { ...current, at };
    seekRequest = { at, key: ++seekKey };
    emit();
  },

  /** The seek waiting for the audio element, once; null when none is. */
  takeSeek(): { at: number; key: number } | null {
    const request = seekRequest;
    seekRequest = null;
    return request;
  },

  markUnplayable(): void {
    if (current === null || current.unplayable) return;
    blocked.add(current.songId);
    current = { ...current, unplayable: true, paused: true };
    setListening(null);
    emit();
  },
  /** 停下来。她的语音开口、通话接通，都会调这个。 */
  stop(): void {
    if (current === null) {
      setListening(null);
      return;
    }
    current = null;
    seekRequest = null;
    setListening(null);
    emit();
  },
};

function randomIndex(): number {
  if (queue.length <= 1) return 0;
  let index = queueIndex;
  while (index === queueIndex) index = Math.floor(Math.random() * queue.length);
  return index;
}
