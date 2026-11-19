"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { SceneLine } from "@/lib/barScene";

/** 队列里的一页（barScene 的 ScenePage 里够这里用的部分）。 */
type QueuedPage = { line: SceneLine };

/**
 * 她的台词在酒吧里也有声音（酒吧语音）：她的每一句（日语原文 `ja`）交给酒吧的 AudioContext 播放
 * （`say`，进门那一下已解锁，不受浏览器自动播放限制）。
 *
 * - **提前合成**：台词还在队列里（`queue`）时就先把它们的 mp3 要过来，轮到她这句时直接播，没有等合成的延迟。
 * - **逐句播**：只有当前显示的这一句（`line`）会播；同一句分了多页时声音跨页继续，只有**换句**（`line` 变了）才停。
 * - **等声音**：当前这句的声音解码并真正开始播放后，才把 `ready` 交给文本框，让文字和声音一起出现。
 * - 同一句按角色 + 原文缓存，不重复合成、不重复播放。
 */
export function useBarVoice({
  line,
  queue,
  characterId,
  enabled,
  say,
  stopSay,
}: {
  line: SceneLine | null;
  queue: QueuedPage[];
  characterId: string | null;
  enabled: boolean;
  say: (blob: Blob) => Promise<void> | void;
  stopSay: () => void;
}) {
  const cache = useRef(new Map<string, Promise<Blob | null>>());
  const played = useRef<string | null>(null);
  /** Whether the current line's voice is ready to be shown together with its text. */
  const [ready, setReady] = useState(true);
  /** When the current line's voice came ready, for the text box to start typing then. */
  const [readyAt, setReadyAt] = useState<number | null>(null);
  /** Which line the ready state belongs to. */
  const [readyKey, setReadyKey] = useState<string | null>(null);

  const load = useCallback(
    (text: string): Promise<Blob | null> => {
      const key = `${characterId}:${text}`;
      const hit = cache.current.get(key);
      if (hit) return hit;
      const pending = fetch("/api/bar/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ character_id: characterId, text }),
      })
        .then((response) => (response.ok ? response.blob() : null))
        .catch(() => null);
      cache.current.set(key, pending);
      return pending;
    },
    [characterId],
  );

  // 台词还在队列里就先合成好，轮到时直接播。
  useEffect(() => {
    if (!enabled || !characterId) return;
    for (const queued of queue) {
      if (queued.line.speaker !== "her") continue;
      if (!queued.line.ja.trim()) continue;
      void load(queued.line.ja);
    }
  }, [enabled, characterId, queue, load]);

  useEffect(() => {
    // 换了一句：先把上一句的声音停掉；同一句只翻页时不动它，声音接着放。
    stopSay();
    setReadyAt(null);
    if (!enabled || !characterId || !line || line.speaker !== "her" || !line.ja.trim()) {
      setReadyKey(null);
      setReady(true);
      return;
    }
    const key = `${characterId}:${line.ja}`;
    if (played.current === key) {
      setReadyKey(key);
      setReady(true);
      return;
    }
    played.current = key;
    setReadyKey(null);
    setReady(false);
    let cancelled = false;
    void load(line.ja).then(async (blob) => {
      if (cancelled) return;
      if (blob) await say(blob);
      if (!cancelled) {
        setReadyAt(performance.now());
        setReadyKey(key);
        setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, characterId, line, say, stopSay, load]);

  return { ready, readyAt, readyKey };
}
