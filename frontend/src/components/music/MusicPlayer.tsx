"use client";

// The shell's player: it owns the one <audio>, so a song keeps playing as pages change.
// The floating bar shows what is playing, lets the user seek, star it, or stop it.

import { useEffect, useRef, useSyncExternalStore } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { copy } from "@/i18n";
import { streamUrl } from "@/lib/music";
import { useMusicFavorites } from "@/lib/musicFavorites";
import { musicPlayback } from "@/lib/musicPlayer";
import { cn } from "@/lib/utils";
import { CloseIcon, FavoriteIcon, MusicIcon } from "@/theme/icons";

const nothing = () => null;

/** Seconds as 0:07 / 5:12. */
function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function MusicPlayer() {
  const playing = useSyncExternalStore(musicPlayback.subscribe, musicPlayback.get, nothing);
  const { call, account } = useDirectory();
  const favorites = useMusicFavorites(account?.id ?? null);
  const audio = useRef<HTMLAudioElement | null>(null);
  // Which of the song's other releases is being tried, after the first fails.
  const alternative = useRef(0);

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
    void element.play().catch(() => musicPlayback.stop());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing?.neteaseId, playing?.unplayable]);

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

  if (playing === null || playing.unplayable) return null;

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
        onEnded={() => musicPlayback.stop()}
        onError={() => {
          const element = audio.current;
          const next = playing.alternatives[alternative.current];
          if (element && next !== undefined) {
            alternative.current += 1;
            element.src = streamUrl(next, playing.title);
            void element.play().catch(() => musicPlayback.stop());
          } else {
            musicPlayback.markUnplayable();
          }
        }}
      />
      {/* A floating mini-player on every page; on phones it sits above the dock. */}
      <div className="pointer-events-none fixed inset-x-0 bottom-14 z-30 flex justify-center px-3 md:bottom-4">
        <div className="pointer-events-auto flex w-full max-w-xl flex-col gap-1 rounded-2xl border bg-background/95 px-3 py-2 shadow-sm backdrop-blur">
          <div className="flex items-center gap-2">
            <MusicIcon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-meta">{playing.title}</span>
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
              <CloseIcon className="size-4" />
            </button>
          </div>
          <div className="flex items-center gap-2">
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
