"use client";

import type { CurrentWeather } from "@/lib/api";
import { copy } from "@/i18n";
import { WEATHER_ICONS } from "@/lib/moments";

/** The producer's weather at the top of the conversation list: icon and whole degrees. */
export function WeatherNow({ weather }: { weather: CurrentWeather | null }) {
  if (!weather) return null;
  const Icon = WEATHER_ICONS[weather.kind];
  return (
    <span
      className="flex shrink-0 items-center gap-1 text-meta text-muted-foreground"
      aria-label={`${copy.moments.weather[weather.kind]} ${weather.temperature}°`}
    >
      <Icon aria-hidden className="size-4" />
      <span aria-hidden>{weather.temperature}°</span>
    </span>
  );
}
