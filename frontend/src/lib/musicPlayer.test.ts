import { beforeEach, describe, expect, it, vi } from "vitest";

import { musicPlayback } from "@/lib/musicPlayer";
import { getListening } from "@/lib/musicListening";

describe("跨页面的歌曲播放状态", () => {
  beforeEach(() => {
    musicPlayback.stop();
  });

  it("一开始什么都没在放", () => {
    expect(musicPlayback.get()).toBeNull();
  });

  it("放一首之后，谁读都能读到同一首（页面换了也读得到）", () => {
    musicPlayback.play("song-1", "虹になれ", "2020881825");
    expect(musicPlayback.get()).toEqual({
      songId: "song-1",
      title: "虹になれ",
      neteaseId: "2020881825",
      alternatives: [],
      at: 0,
      duration: 0,
      paused: false,
      singers: [],
      unplayable: false,
    });
  });

  it("已经在放同一首时不重复通知", () => {
    const listener = vi.fn();
    const unsubscribe = musicPlayback.subscribe(listener);
    musicPlayback.play("song-1", "虹になれ", "1");
    musicPlayback.play("song-1", "虹になれ", "1");
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("换一首会通知", () => {
    const listener = vi.fn();
    const unsubscribe = musicPlayback.subscribe(listener);
    musicPlayback.play("song-1", "虹になれ", "1");
    musicPlayback.play("song-1", "ヒカリのdestination", "2");
    expect(listener).toHaveBeenCalledTimes(2);
    expect(musicPlayback.get()?.title).toBe("ヒカリのdestination");
    unsubscribe();
  });

  it("进度按秒通知，同一秒不重复", () => {
    const listener = vi.fn();
    const unsubscribe = musicPlayback.subscribe(listener);
    musicPlayback.play("song-1", "虹になれ", "1");
    expect(listener).toHaveBeenCalledTimes(1);

    musicPlayback.tick(0.4, 313);   // 秒数没变，但时长第一次知道 → 通知
    expect(listener).toHaveBeenCalledTimes(2);
    musicPlayback.tick(0.9, 313);   // 同一秒 → 不通知
    expect(listener).toHaveBeenCalledTimes(2);
    musicPlayback.tick(1.2, 313);   // 进到第 1 秒 → 通知
    expect(listener).toHaveBeenCalledTimes(3);
    expect(musicPlayback.get()?.at).toBe(1);
    expect(musicPlayback.get()?.duration).toBe(313);
    unsubscribe();
  });

  it("取不到的那条标记成放不了，再点一次还能重试", () => {
    musicPlayback.play("song-1", "虹になれ", "1");
    musicPlayback.markUnplayable();
    expect(musicPlayback.get()?.unplayable).toBe(true);

    const listener = vi.fn();
    const unsubscribe = musicPlayback.subscribe(listener);
    musicPlayback.play("song-1", "虹になれ", "1");   // 同一首，但之前失败了 → 重新来
    expect(listener).toHaveBeenCalledTimes(1);
    expect(musicPlayback.get()?.unplayable).toBe(false);
    unsubscribe();
  });

  it("停下之后回到没有，且没人订阅了就不再管", () => {
    musicPlayback.play("song-1", "虹になれ", "1");
    musicPlayback.stop();
    expect(musicPlayback.get()).toBeNull();

    const listener = vi.fn();
    const unsubscribe = musicPlayback.subscribe(listener);
    unsubscribe();
    musicPlayback.play("song-1", "虹になれ", "1");
    expect(listener).not.toHaveBeenCalled();
  });

  it("seek moves the shown position and leaves one request for the audio element", () => {
    musicPlayback.play("song-1", "song", "1");
    musicPlayback.tick(10, 100);

    musicPlayback.seek(42);

    expect(musicPlayback.get()?.at).toBe(42);
    expect(musicPlayback.takeSeek()).toEqual({ at: 42, key: 1 });
    expect(musicPlayback.takeSeek()).toBeNull();
  });

  it("keeps the list it was picked from and cycles the play mode", () => {
    musicPlayback.play(
      "song-1",
      "one",
      "1",
      [],
      [
        { songId: "song-1", title: "one", singers: [] },
        { songId: "song-2", title: "two", singers: [] },
      ],
      0,
    );

    expect(musicPlayback.queueLength()).toBe(2);
    const before = musicPlayback.mode();
    musicPlayback.cycleMode();
    expect(musicPlayback.mode()).not.toBe(before);
  });

  it("keeps the prompt's listening line only while the song is playing", () => {
    musicPlayback.play("song-1", "星", "1", [], undefined, 0, ["大崎甘奈"]);
    expect(getListening()).toBe("星 · 大崎甘奈");
    musicPlayback.pause();
    expect(getListening()).toBeNull();
    musicPlayback.resume();
    expect(getListening()).toBe("星 · 大崎甘奈");
    musicPlayback.stop();
    expect(getListening()).toBeNull();
  });

  it("pauses and resumes without losing the song", () => {
    musicPlayback.play("song-1", "song", "1");

    musicPlayback.pause();
    expect(musicPlayback.get()?.paused).toBe(true);

    musicPlayback.togglePaused();
    expect(musicPlayback.get()?.paused).toBe(false);
  });
});
