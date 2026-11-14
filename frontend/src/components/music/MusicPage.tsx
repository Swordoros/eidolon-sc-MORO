"use client";

// 歌曲页（fork）：上面一排她的头像，点一位就只看她唱过的歌；切到「专辑」按发售日看碟。
// 播放靠网易云的外链播放器，点一首才去查它在网易云是哪一首（查到就记住，不再问）。

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";

import { Button } from "@/components/ui/button";
import { PlayShell, PlaySubpageHeader } from "@/components/play/PlayShell";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import { api, json, type Character } from "@/lib/api";
import {
  musicAlbums,
  musicLookup,
  musicSongs,
  type Album,
  type FoundSong,
  type Song,
} from "@/lib/music";
import { useMusicFavorites } from "@/lib/musicFavorites";
import { musicPlayback } from "@/lib/musicPlayer";
import { cn } from "@/lib/utils";
import { AlbumIcon, FavoriteIcon, MusicIcon, PlayIcon, SearchIcon } from "@/theme/icons";

const t = copy.music;

type Tab = "songs" | "albums" | "favorites";

export function MusicPage() {
  const [characters, setCharacters] = useState<Character[] | null>(null);
  const [singers, setSingers] = useState<string[]>([]);
  const [songs, setSongs] = useState<Song[] | null>(null);
  const [allSongs, setAllSongs] = useState<Song[] | null>(null);
  const [albums, setAlbums] = useState<Album[] | null>(null);
  const [tab, setTab] = useState<Tab>("songs");
  const [picked, setPicked] = useState<string | null>(null);
  /** 点了但没查到的那一条：展开告诉用户「网易云没有这首」。 */
  const [nowPlaying, setNowPlaying] = useState<string | null>(null);
  // 在放哪一首由外壳的播放器说了算：离开这一页它还在放
  const now = useSyncExternalStore(musicPlayback.subscribe, musicPlayback.get, () => null);
  const [found, setFound] = useState<Record<string, FoundSong | null>>({});
  const [searching, setSearching] = useState<string | null>(null);
  const [openAlbum, setOpenAlbum] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"time" | "name">("time");
  const [ascending, setAscending] = useState(false);
  const { account } = useDirectory();
  const favorites = useMusicFavorites(account?.id ?? null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const [listed, shelf, shelfAlbums] = await Promise.all([
        json(await api("/api/characters")) as Promise<Character[]>,
        musicSongs(),
        musicAlbums(),
      ]);
      if (!alive) return;
      setCharacters(listed);
      setSingers(shelf.singers);
      setSongs(shelf.songs);
      setAllSongs(shelf.songs);
      setAlbums(shelfAlbums.albums);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 只留有歌的那几位，按世界设定里的顺序（不是角色的顺序）
  const withSongs = useMemo(() => {
    if (!characters) return [];
    const byName = new Map(characters.map((character) => [character.name, character]));
    return singers.flatMap((name) => {
      const character = byName.get(name);
      return character ? [character] : [];
    });
  }, [characters, singers]);

  useEffect(() => {
    if (picked === null) return;
    let alive = true;
    void (async () => {
      const shelf = await musicSongs(picked);
      if (alive) setSongs(shelf.songs);
    })();
    return () => {
      alive = false;
    };
  }, [picked]);


  /** 点一首：先查它在网易云是哪一首，再交给外壳的播放器（切页面也不停）。 */
  async function play(song: Song) {
    const title = song.title;
    if (now?.songId === song.id) {
      musicPlayback.togglePaused();
      return;
    }
    let hit = found[title];
    if (hit === undefined) {
      setSearching(title);
      try {
        hit = await musicLookup(title);
        setFound((before) => ({ ...before, [title]: hit }));
      } finally {
        setSearching(null);
      }
    }
    if (hit === null) {
      setNowPlaying(title);   // 没查到：展开那句「网易云没有这首」
      return;
    }
    const list = tab === "favorites" ? favoriteSongs : visibleSongs;
    const queue = list.map((item) => ({ songId: item.id, title: item.title }));
    const index = Math.max(0, list.findIndex((item) => item.id === song.id));
    musicPlayback.play(song.id, title, hit.id, hit.alternatives ?? [], queue, index);
  }

  const albumTitles = useMemo(
    () => new Map((albums ?? []).map((album) => [album.catalog, album.title])),
    [albums],
  );
  const releasedAt = useMemo(
    () => new Map((albums ?? []).map((album) => [album.catalog, Date.parse(album.date)])),
    [albums],
  );
  const dateOf = (song: Song) =>
    Math.max(0, ...song.releases.map((catalog) => releasedAt.get(catalog) ?? 0));
  const needle = query.trim().toLocaleLowerCase();
  const visibleSongs = useMemo(() => {
    const matched = (songs ?? []).filter((song) => {
      if (!needle) return true;
      return [song.title, ...song.singers, song.special_unit ?? "",
        ...song.releases.map((catalog) => albumTitles.get(catalog) ?? "")]
        .join(" ").toLocaleLowerCase().includes(needle);
    });
    return [...matched].sort((a, b) => {
      const order = sort === "time"
        ? dateOf(a) - dateOf(b)
        : a.title.localeCompare(b.title, "ja");
      return ascending ? order : -order;
    });
  }, [songs, needle, sort, ascending, releasedAt, albumTitles]);
  const allVisibleSongs = useMemo(() => {
    const matched = (allSongs ?? []).filter((song) => {
      if (!needle) return true;
      return [song.title, ...song.singers, song.special_unit ?? "",
        ...song.releases.map((catalog) => albumTitles.get(catalog) ?? "")]
        .join(" ").toLocaleLowerCase().includes(needle);
    });
    return [...matched].sort((a, b) => {
      const order = sort === "time"
        ? dateOf(a) - dateOf(b)
        : a.title.localeCompare(b.title, "ja");
      return ascending ? order : -order;
    });
  }, [allSongs, needle, sort, ascending, releasedAt, albumTitles]);
  const favoriteSongs = useMemo(
    () => allVisibleSongs.filter((song) => favorites.ids.has(song.id)),
    [allVisibleSongs, favorites.ids],
  );
  const chooseSort = (next: "time" | "name") => {
    if (next === sort) setAscending((value) => !value);
    else {
      setSort(next);
      setAscending(next === "name");
    }
  };

  const filterControls = (
    <div className="flex shrink-0 flex-wrap items-center gap-2 px-4 pt-3">
      <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg bg-muted px-3 text-muted-foreground has-focus-visible:ring-3 has-focus-visible:ring-ring/50">
        <SearchIcon aria-hidden className="size-4 shrink-0" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t.search}
          aria-label={t.search}
          className="min-w-0 flex-1 bg-transparent text-body text-foreground outline-none placeholder:text-muted-foreground"
        />
      </label>
      <div className="flex gap-1">
        <Button
          size="sm"
          variant={sort === "time" ? "secondary" : "ghost"}
          aria-pressed={sort === "time"}
          onClick={() => chooseSort("time")}
        >
          {t.sortTime}
          <span className="sr-only">{ascending ? t.sortOldest : t.sortNewest}</span>
        </Button>
        <Button
          size="sm"
          variant={sort === "name" ? "secondary" : "ghost"}
          aria-pressed={sort === "name"}
          onClick={() => chooseSort("name")}
        >
          {t.sortName}
          <span className="sr-only">{ascending ? t.sortAZ : t.sortZA}</span>
        </Button>
      </div>
    </div>
  );

  const current = picked === null ? null : withSongs.find((character) => character.id === picked) ?? null;

  const songRows = (items: Song[]) => items.map((song) => {
    const starred = favorites.ids.has(song.id);
    return (
      <li key={song.id} className="py-2">
        <div className="flex items-center gap-3">
          <Button
            size="icon-sm"
            variant={now?.songId === song.id ? "secondary" : "ghost"}
            aria-label={now?.songId === song.id ? t.stop : t.play(song.title)}
            onClick={() => void play(song)}
          >
            <PlayIcon />
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-body font-medium">{song.title}</p>
            <p className="truncate text-meta text-muted-foreground">
              {song.singers.join(" · ")}
              {song.special_unit ? ` · ${song.special_unit}` : ""}
            </p>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={starred ? t.unstar(song.title) : t.star(song.title)}
            aria-pressed={starred}
            onClick={() => void favorites.toggle(song.id).catch(() => {})}
          >
            <FavoriteIcon className={cn("size-4", starred && "fill-current")} />
          </Button>
        </div>
        {(now?.songId === song.id || nowPlaying === song.title) && (
          <div className="mt-2 ps-12">
            {searching === song.title ? (
              <p className="text-meta text-muted-foreground">{t.searching}</p>
            ) : now?.songId === song.id && now.unplayable ? (
              <p className="text-meta text-muted-foreground">{t.cannotPlay}</p>
            ) : found[song.title] === null ? (
              <p className="text-meta text-muted-foreground">{t.notOnNetease}</p>
            ) : null}
          </div>
        )}
      </li>
    );
  });

  return (
    <PlayShell header={<PlaySubpageHeader title={t.title} />}>
      <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        {current && tab === "songs" && (
          <span className="text-meta text-muted-foreground">{t.only(current.name_zh ?? current.name)}</span>
        )}
        <div className="ms-auto flex gap-1">
          <Button
            size="sm"
            variant={tab === "songs" ? "secondary" : "ghost"}
            onClick={() => setTab("songs")}
          >
            <MusicIcon data-icon="inline-start" />
            {t.songsTab}
          </Button>
          <Button
            size="sm"
            variant={tab === "albums" ? "secondary" : "ghost"}
            onClick={() => setTab("albums")}
          >
            <AlbumIcon data-icon="inline-start" />
            {t.albumsTab}
          </Button>
          <Button
            size="sm"
            variant={tab === "favorites" ? "secondary" : "ghost"}
            onClick={() => setTab("favorites")}
          >
            <FavoriteIcon data-icon="inline-start" />
            {t.favorites}
          </Button>
        </div>
      </div>

      {tab === "songs" && (
        <>
          <div className="flex shrink-0 gap-3 overflow-x-auto border-b px-4 py-3">
            <button
              type="button"
              aria-pressed={picked === null}
              onClick={() => setPicked(null)}
              className={cn(
                "flex shrink-0 flex-col items-center gap-1 rounded-lg px-2 py-1 text-meta",
                picked === null ? "bg-accent text-foreground" : "text-muted-foreground",
              )}
            >
              <span className="flex size-12 items-center justify-center rounded-full border bg-background">
                <MusicIcon className="size-5" />
              </span>
              {t.everyone}
            </button>
            {withSongs.map((character) => (
              <button
                key={character.id}
                type="button"
                aria-pressed={picked === character.id}
                onClick={() => setPicked(character.id)}
                className={cn(
                  "flex w-16 shrink-0 flex-col items-center gap-1 rounded-lg px-1 py-1 text-meta",
                  picked === character.id ? "bg-accent text-foreground" : "text-muted-foreground",
                )}
              >
                {character.avatar_url ?? character.scsp_avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={(character.avatar_url ?? character.scsp_avatar_url) as string}
                    alt=""
                    className="size-12 rounded-full border object-cover"
                    style={{ borderColor: character.color }}
                  />
                ) : (
                  <span className="size-12 rounded-full border bg-muted" />
                )}
                <span className="w-full truncate text-center">{character.name_zh ?? character.name}</span>
              </button>
            ))}
          </div>

          {filterControls}

          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-28">
            {songs === null ? (
              <div className="flex h-full items-center justify-center">
                <Spinner className="size-6 text-muted-foreground" aria-label={t.loading} />
              </div>
            ) : songs.length === 0 ? (
              <p className="py-10 text-center text-body text-muted-foreground">{t.noSongs}</p>
            ) : (
              <section aria-labelledby="all-songs">
                  <h2 id="all-songs" className="mb-1 text-body font-medium">{t.allSongs}</h2>
                  {visibleSongs.length > 0 ? (
                    <ul className="divide-y">{songRows(visibleSongs)}</ul>
                  ) : (
                    <p className="py-2 text-meta text-muted-foreground">{t.noResults}</p>
                  )}
              </section>
            )}
          </div>
        </>
      )}

      {tab === "favorites" && (
        <>
          {filterControls}
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-28">
            {allSongs === null ? (
              <div className="flex h-full items-center justify-center">
                <Spinner className="size-6 text-muted-foreground" aria-label={t.loading} />
              </div>
            ) : favoriteSongs.length === 0 ? (
              <p className="py-10 text-center text-body text-muted-foreground">
                {query.trim() ? t.noResults : t.noFavorites}
              </p>
            ) : (
              <>
                {favorites.failed && (
                  <p className="mb-2 text-meta text-muted-foreground">{t.favoriteFailed}</p>
                )}
                <ul className="divide-y">{songRows(favoriteSongs)}</ul>
              </>
            )}
          </div>
        </>
      )}

      {tab === "albums" && (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6">
          {albums === null ? (
            <div className="flex h-full items-center justify-center">
              <Spinner className="size-6 text-muted-foreground" aria-label={t.loading} />
            </div>
          ) : (
            <ul className="divide-y">
              {albums.map((album) => (
                <li key={album.catalog} className="py-2">
                  <button
                    type="button"
                    className="flex w-full items-baseline gap-3 text-start"
                    onClick={() => setOpenAlbum(openAlbum === album.catalog ? null : album.catalog)}
                  >
                    <span className="text-meta text-muted-foreground tabular-nums">{album.date}</span>
                    <span className="min-w-0 flex-1 truncate text-body">{album.title}</span>
                    <span className="text-meta text-muted-foreground">{album.catalog}</span>
                  </button>
                  {openAlbum === album.catalog && (
                    <ol className="mt-2 ms-16 list-decimal space-y-1 text-meta text-muted-foreground">
                      {album.tracks.map((track, index) => (
                        <li key={`${album.catalog}-${index}`}>{track}</li>
                      ))}
                    </ol>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      </div>
    </PlayShell>
  );
}
