// 对话页「日程安排」：读她今天的行程，排成一条按时间走的列表。
// 后端 `/api/characters/{id}/schedule` 不给日期时按制作人的有效时区取今天。

import { api, json } from "@/lib/api";
import { copy } from "@/i18n";

const t = copy.conversation.agenda;

export type AgendaSlot = {
  start: string;
  end: string;
  what: string;
  where: string;
  kind: string;
  with: string[];
};

export type DayAgenda = {
  day: string;
  slots: AgendaSlot[];
  twists: string[];
  wake: string | null;
  sleep: string | null;
  bath: { at: string; minutes: number } | null;
  trip: string | null;
  trip_units?: string[];
};

export type AgendaRow = {
  key: string;
  at: number;
  time: string;
  what: string;
  where?: string;
};

export async function getDayAgenda(characterId: string): Promise<DayAgenda | null> {
  return json(await api(`/api/characters/${encodeURIComponent(characterId)}/schedule`));
}

const minutesOf = (clock: string): number => {
  const [hours, minutes] = clock.split(":").map(Number);
  return hours * 60 + minutes;
};

/** 00:30 这种过了午夜的时刻排在一天最后，而不是最前。 */
const afterMidnight = (clock: string): number => {
  const at = minutesOf(clock);
  return at < 12 * 60 ? at + 24 * 60 : at;
};

/** 当天日程：起床、各段安排、洗澡、睡觉，按时间先后。没有行程时是空列表。 */
export function agendaRows(agenda: DayAgenda | null): AgendaRow[] {
  if (agenda === null) return [];
  const rows: AgendaRow[] = [];
  if (agenda.wake !== null) {
    rows.push({ key: "wake", at: minutesOf(agenda.wake), time: agenda.wake, what: t.wake });
  }
  agenda.slots.forEach((slot, index) => {
    rows.push({
      key: `slot:${index}`,
      at: minutesOf(slot.start),
      time: `${slot.start}–${slot.end}`,
      what: slot.what,
      where: slot.where || undefined,
    });
  });
  if (agenda.bath !== null) {
    rows.push({
      key: "bath",
      at: afterMidnight(agenda.bath.at),
      time: agenda.bath.at,
      what: t.bath,
    });
  }
  if (agenda.sleep !== null) {
    rows.push({
      key: "sleep",
      at: afterMidnight(agenda.sleep),
      time: agenda.sleep,
      what: t.sleep,
    });
  }
  return rows.sort((a, b) => a.at - b.at);
}
