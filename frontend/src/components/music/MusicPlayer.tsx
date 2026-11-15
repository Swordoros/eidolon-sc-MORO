"use client";

// The shell's player: it owns the one <audio>, so a song keeps playing as pages change.
// The floating bar shows what is playing, lets the user seek, star it, or stop it.

import { useEffect, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { copy } from "@/i18n";
import { streamUrl } from "@/lib/music";
import { useMusicFavorites } from "@/lib/musicFavorites";
import { loadMusicVolume, saveMusicVolume } from "@/lib/musicVolume";
import { loadPlayerPosition, savePlayerPosition, type PlayerPosition } from "@/lib/musicPlayerPosition";
import { musicPlayback } from "@/lib/musicPlayer";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  DragHandleIcon, FavoriteIcon, MusicIcon, MusicMutedIcon, MusicPauseIcon, MusicPlayIcon,
  MusicStopIcon, MusicVolumeIcon, NextSongIcon, PreviousSongIcon, RepeatOneModeIcon,
  SequenceModeIcon, ShuffleModeIcon,
} from "@/theme/icons";

const nothing = () => null;

/** Seconds as 0:07 / 5:12. */
function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** The title and singers, scrolling only when they do not fit the bar. */
function Marquee({ text }: { text: string }) {
  const outer = useRef<HTMLDivElement | null>(null);
  const first = useRef<HTMLSpanElement | null>(null);
  const [overflow, setOverflow] = useState(false);

  useEffect(() => {
    const box = outer.current;
    const content = first.current;
    if (!box || !content) return;
    const measure = () => setOverflow(content.scrollWidth > box.clientWidth + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [text]);

  return (
    <div ref={outer} className="min-w-0 flex-1 overflow-hidden">
      <div className={cn("flex w-max whitespace-nowrap", overflow && "animate-marquee motion-reduce:animate-none")}>
        <span ref={first}>{text}</span>
        {overflow && <span aria-hidden className="ps-8">{text}</span>}
      </div>
    </div>
  );
}

export function MusicPlayer() {
  const playing = useSyncExternalStore(musicPlayback.subscribe, musicPlayback.get, nothing);
  const { call, account } = useDirectory();
  const favorites = useMusicFavorites(account?.id ?? null);
  const [volume, setVolume] = useState(loadMusicVolume);
  const [position, setPosition] = useState<PlayerPosition | null>(loadPlayerPosition);
  const audio = useRef<HTMLAudioElement | null>(null);
  const panel = useRef<HTMLDivElement | null>(null);
  const positionRef = useRef(position);
  positionRef.current = position;
  const drag = useRef<{ pointerId: number; dx: number; dy: number } | null>(null);
  // Which of the song's other releases is being tried, after the first fails.
  const alternative = useRef(0);

  const clampPosition = (x: number, y: number): PlayerPosition => {
    const width = panel.current?.offsetWidth ?? 0;
    const height = panel.current?.offsetHeight ?? 0;
    return {
      x: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - height - 8)),
    };
  };

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !panel.current) return;
    const rect = panel.current.getBoundingClientRect();
    drag.current = {
      pointerId: event.pointerId,
      dx: event.clientX - rect.left,
      dy: event.clientY - rect.top,
    };
    setPosition({ x: rect.left, y: rect.top });
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };

  const moveDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    setPosition(clampPosition(event.clientX - active.dx, event.clientY - active.dy));
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  useEffect(() => {
    if (position) savePlayerPosition(position);
  }, [position]);

  useEffect(() => {
    const onResize = () => {
      const current = positionRef.current;
      if (current) setPosition(clampPosition(current.x, current.y));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // A stored position may be off-screen after a viewport change.
  useEffect(() => {
    const saved = positionRef.current;
    if (!saved) return;
    setPosition(clampPosition(saved.x, saved.y));
  }, [playing?.neteaseId]);

  // Starting a call stops it: the microphone and the call need the audio.
  useEffect(() => {
    if (call !== null) musicPlayback.stop();
  }, [call]);

  // Only a new song or a failed stream changes src; not each tick.
  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    alternative.current = 0;
    if (playing === null) {
      element.pause();
      element.removeAttribute("src");
      element.load();
      return;
    }
    if (playing.unplayable) return;
    element.src = streamUrl(playing.neteaseId, playing.title);
    if (playing.paused) {
      element.pause();
    } else {
      void element.play().catch(() => musicPlayback.stop());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing?.neteaseId, playing?.unplayable]);

  useEffect(() => {
    const element = audio.current;
    if (!element || !playing || playing.unplayable) return;
    if (playing.paused) element.pause();
    else void element.play().catch(() => musicPlayback.stop());
  }, [playing?.paused, playing?.neteaseId, playing?.unplayable]);

  // A seek is applied once, after the render that asked for it.
  useEffect(() => {
    const request = musicPlayback.takeSeek();
    const element = audio.current;
    if (!request || !element) return;
    try {
      element.currentTime = request.at;
    } catch {
      // The stream is not seekable yet; the next drag will try again.
    }
  }, [playing]);

  // Volume is a device preference; set it whenever the audio element or slider changes.
  useEffect(() => {
    if (audio.current) audio.current.volume = volume;
  }, [volume, playing?.neteaseId]);

  if (playing === null) return null;

  const starred = playing.songId ? favorites.ids.has(playing.songId) : false;
  const duration = playing.duration > 0 ? playing.duration : 0;

  return (
    <>
      {/* The real sound: hidden, and never unmounted while a song is playing. */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio
        ref={audio}
        preload="none"
        onTimeUpdate={(event) =>
          musicPlayback.tick(event.currentTarget.currentTime, event.currentTarget.duration || 0)
        }
        onLoadedMetadata={(event) =>
          musicPlayback.tick(event.currentTarget.currentTime, event.currentTarget.duration || 0)
        }
        onEnded={() => {
          const element = audio.current;
          if (musicPlayback.mode() === "repeat-one" && element) {
            element.currentTime = 0;
            void element.play().catch(() => musicPlayback.stop());
          } else {
            void musicPlayback.next();
          }
        }}
        onError={() => {
          const element = audio.current;
          const next = playing.alternatives[alternative.current];
          if (element && next !== undefined) {
            alternative.current += 1;
            element.src = streamUrl(next, playing.title);
            void element.play().catch(() => musicPlayback.stop());
          } else {
            musicPlayback.markUnplayable();
            void musicPlayback.next();
          }
        }}
      />
      {/* A floating mini-player on every page; on phones it sits above the dock. It can be
          dragged by its title bar so it no longer covers the input box. */}
      <div
        className={cn(
          "pointer-events-none fixed z-30 px-3",
          position === null
            ? "inset-x-0 bottom-14 flex justify-end md:bottom-4"
            : "w-[min(36rem,calc(100vw-1.5rem))]",
        )}
        style={position ? { left: position.x, top: position.y } : undefined}
      >
        <div ref={panel} className="pointer-events-auto flex w-full max-w-xl flex-col gap-1 rounded-2xl border bg-background/95 px-3 py-2 shadow-sm backdrop-blur">
          <div className="flex items-center gap-2">
            <div
              role="button"
              tabIndex={0}
              aria-label={copy.music.move}
              title={copy.music.move}
              className="flex min-w-0 flex-1 cursor-grab touch-none select-none items-center gap-2 active:cursor-grabbing"
              onPointerDown={startDrag}
              onPointerMove={moveDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              <DragHandleIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              <MusicIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              <Marquee
                text={playing.singers.length > 0
                  ? `${playing.title} · ${playing.singers.join(" / ")}`
                  : playing.title}
              />
            </div>
          </div>
          <div className="flex items-center justify-center gap-0.5">
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                aria-label={copy.music.previous}
                disabled={musicPlayback.queueLength() < 2}
                className="rounded-full p-1 text-muted-foreground hover:text-foreground disabled:opacity-35"
                onClick={() => void musicPlayback.previous()}
              >
                <PreviousSongIcon className="size-4" />
              </button>
              <button
                type="button"
                aria-label={playing.paused ? copy.music.resume : copy.music.pause}
                className="rounded-full p-1 text-muted-foreground hover:text-foreground"
                onClick={() => musicPlayback.togglePaused()}
              >
                {playing.paused ? <MusicPlayIcon className="size-4" /> : <MusicPauseIcon className="size-4" />}
              </button>
              <button
                type="button"
                aria-label={copy.music.next}
                disabled={musicPlayback.queueLength() < 2}
                className="rounded-full p-1 text-muted-foreground hover:text-foreground disabled:opacity-35"
                onClick={() => void musicPlayback.next()}
              >
                <NextSongIcon className="size-4" />
              </button>
              <button
                type="button"
                aria-label={copy.music.modes[musicPlayback.mode()]}
                title={copy.music.modes[musicPlayback.mode()]}
                className="rounded-full p-1 text-muted-foreground hover:text-foreground"
                onClick={() => musicPlayback.cycleMode()}
              >
                {musicPlayback.mode() === "repeat-one" ? (
                  <RepeatOneModeIcon className="size-4" />
                ) : musicPlayback.mode() === "shuffle" ? (
                  <ShuffleModeIcon className="size-4" />
                ) : (
                  <SequenceModeIcon className="size-4" />
                )}
              </button>
            </div>
            <Popover>
              <PopoverTrigger
                render={
                  <button
                    type="button"
                    aria-label={copy.music.volume}
                    className="rounded-full p-1 text-muted-foreground hover:text-foreground"
                  />
                }
              >
                {volume === 0 ? <MusicMutedIcon className="size-4" /> : <MusicVolumeIcon className="size-4" />}
              </PopoverTrigger>
              <PopoverContent side="top" align="end" className="w-56">
                <div className="flex items-center gap-2">
                  {volume === 0 ? <MusicMutedIcon className="size-4" /> : <MusicVolumeIcon className="size-4" />}
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round(volume * 100)}
                    aria-label={copy.music.volume}
                    onChange={(event) => {
                      const next = Number(event.target.value) / 100;
                      setVolume(next);
                      saveMusicVolume(next);
                    }}
                    className="h-1 min-w-0 flex-1 cursor-pointer accent-foreground"
                  />
                  <span className="w-8 text-right text-meta text-muted-foreground tabular-nums">
                    {Math.round(volume * 100)}
                  </span>
                </div>
              </PopoverContent>
            </Popover>
            {playing.songId && (
              <button
                type="button"
                aria-label={starred ? copy.music.unstar(playing.title) : copy.music.star(playing.title)}
                aria-pressed={starred}
                className="rounded-full p-1 text-muted-foreground hover:text-foreground"
                onClick={() => void favorites.toggle(playing.songId).catch(() => {})}
              >
                <FavoriteIcon className={cn("size-4", starred && "fill-current")} />
              </button>
            )}
            <button
              type="button"
              aria-label={copy.music.stop}
              className="rounded-full p-1 text-muted-foreground hover:text-foreground"
              onClick={() => musicPlayback.stop()}
            >
              <MusicStopIcon className="size-4" />
            </button>
          </div>
          <div className="flex items-center gap-2">
            {playing.unplayable && (
              <span className="shrink-0 text-meta text-muted-foreground">{copy.music.cannotPlay}</span>
            )}
            <span className="w-9 shrink-0 text-right text-meta text-muted-foreground tabular-nums">
              {clock(playing.at)}
            </span>
            <input
              type="range"
              min={0}
              max={duration || 0}
              step={1}
              value={duration ? Math.min(playing.at, duration) : 0}
              disabled={duration === 0}
              aria-label={copy.music.seek}
              onChange={(event) => musicPlayback.seek(Number(event.target.value))}
              className="h-1 min-w-0 flex-1 cursor-pointer accent-foreground disabled:cursor-default"
            />
            <span className="w-9 shrink-0 text-meta text-muted-foreground tabular-nums">
              {duration > 0 ? clock(duration) : "—"}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
