// 日历（玩法页的「日历」卡和子页 `/play/calendar`，GLOSSARY.md「日历」）：角色的生日、制作人的生日、
// 日本节假日，以及日程的周视图里的工作，按日子排成月视图的格子和一天的清单。
//
// 哪天是「今天」按制作人的时区算，和对话里她们知道的「今天」是同一天。月视图星期一在最左边，不是当月的格子
// 照常显示（淡下去）。开哪几类存在这台设备上（lib/calendarFilter），月 / 周切换的选法也存在这台设备上。

import type { Birthday } from "@/lib/api";

/** 一年中的某月：年和月。 */
export type CalendarMonth = { year: number; month: number };

/** 月视图里的一格：是哪一天，是不是今天，是不是当月的日子（不是的是补在月首月尾的邻月日）。 */
export type CalendarCell = { date: string; isToday: boolean; isInMonth: boolean };

/** 日历上的一类日子：角色生日、制作人的生日（界面上「我的生日」）、节假日、日程。 */
export type CalendarKind = "character" | "producer" | "holiday" | "schedule";

/** 每类的顺序，也是筛选按钮的顺序。 */
export const CALENDAR_KINDS: readonly CalendarKind[] = ["character", "producer", "holiday", "schedule"];

/** 月视图还是周视图。 */
export type CalendarMode = "month" | "week";

/** 一个日本节假日：哪一天和它的日文名。 */
export type Holiday = { date: string; name: string };

/** 某一天的一段工作（日程）：角色、是哪一项、叫什么、哪天哪几段，和保存了没有。 */
export type DayJob<C> = {
  date: string;
  character: C;
  jobId: string;
  name: string;
  slotsText: string;
  isSaved: boolean;
};

/** 某一天里的一件事：一位角色的生日、制作人的生日、一个节假日，或一段工作。 */
export type CalendarEvent<C> =
  | { kind: "character"; character: C }
  | { kind: "producer" }
  | { kind: "holiday"; name: string }
  | { kind: "schedule"; job: DayJob<C> };

/** 日历给的数据：角色（带生日）、制作人的生日、节假日，和（周视图里才有的）工作。 */
export type CalendarData<C extends { birthday: string }> = {
  characters: C[];
  producerBirthday: Birthday | null;
  holidays: Holiday[];
  jobs?: DayJob<C>[];
};

/** 玩法页「日历」卡和子页共用的那一份：今天、数据、开着的几类。 */
export type CalendarView<C extends { birthday: string }> = {
  today: string;
  data: CalendarData<C>;
  shown: CalendarKind[];
};

/** 某个时区现在是哪一天（`YYYY-MM-DD`）。 */
export function todayIn(timeZone: string, now: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** 一天（`YYYY-MM-DD`）的各个部分：年月日，和星期几（星期一为 0）。 */
export function partsOf(date: string): { year: number; month: number; day: number; weekday: number } {
  const [year, month, day] = date.split("-").map(Number);
  const weekday = (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
  return { year, month, day, weekday };
}

/** 这一天是哪年哪月。 */
export function monthOf(date: string): CalendarMonth {
  const { year, month } = partsOf(date);
  return { year, month };
}

/** `date` 往后（或往前）`days` 天，还是 `YYYY-MM-DD`。 */
export function addDays(date: string, days: number): string {
  const { year, month, day } = partsOf(date);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
}

/** 上个月或下个月（`step` 为 -1 或 1），跨年时年份跟着变。 */
export function shiftMonth(month: CalendarMonth, step: number): CalendarMonth {
  const index = month.year * 12 + (month.month - 1) + step;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** 月视图的格子，星期一在最左边，首尾补上邻月的日子，凑成整周。 */
export function monthCells(month: CalendarMonth, today: string): CalendarCell[] {
  const pad = (n: number) => String(n).padStart(2, "0");
  const first = `${month.year}-${pad(month.month)}-01`;
  const lead = partsOf(first).weekday;
  const days = daysInMonth(month.year, month.month);
  const cells: CalendarCell[] = [];
  for (let offset = -lead; offset < days; offset++) {
    const date = addDays(first, offset);
    cells.push({ date, isToday: date === today, isInMonth: offset >= 0 });
  }
  while (cells.length % 7 !== 0) {
    cells.push({ date: addDays(cells[cells.length - 1].date, 1), isToday: false, isInMonth: false });
  }
  return cells;
}

function isLeap(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

/** 生日（`MM-DD`，或制作人的 `{month, day}`）的月日。 */
function monthDayOf(birthday: string | Birthday): { month: number; day: number } {
  if (typeof birthday === "string") {
    const [month, day] = birthday.split("-").map(Number);
    return { month, day };
  }
  return birthday;
}

/** 生日是不是落在 `date` 这天：2 月 29 日的生日在平年落在 2 月 28 日。 */
function birthdayOn(birthday: string | Birthday, date: string): boolean {
  const { month, day } = monthDayOf(birthday);
  const today = partsOf(date);
  if (today.month === month && today.day === day) return true;
  return month === 2 && day === 29 && today.month === 2 && today.day === 28 && !isLeap(today.year);
}

/** 日历上有哪几类：角色生日和节假日一直有，制作人的生日有填才有，日程开着才有。 */
export function calendarKinds(producerBirthday: Birthday | null, schedulesOn: boolean): CalendarKind[] {
  return CALENDAR_KINDS.filter(
    (kind) => (kind !== "producer" || producerBirthday !== null) && (kind !== "schedule" || schedulesOn),
  );
}

/** 某一天里开着的几类的事，按类别的顺序；同类的按给的顺序。 */
export function eventsOn<C extends { birthday: string }>(
  date: string,
  data: CalendarData<C>,
  visible: CalendarKind[],
): CalendarEvent<C>[] {
  const events: CalendarEvent<C>[] = [];
  if (visible.includes("character")) {
    for (const character of data.characters) {
      if (birthdayOn(character.birthday, date)) events.push({ kind: "character", character });
    }
  }
  if (visible.includes("producer") && data.producerBirthday && birthdayOn(data.producerBirthday, date)) {
    events.push({ kind: "producer" });
  }
  if (visible.includes("holiday")) {
    for (const holiday of data.holidays) {
      if (holiday.date === date) events.push({ kind: "holiday", name: holiday.name });
    }
  }
  if (visible.includes("schedule") && data.jobs) {
    for (const job of data.jobs) {
      if (job.date === date) events.push({ kind: "schedule", job });
    }
  }
  return events;
}

/** 电脑上格子里的几行（最多三行），多的写「还有 n 项」。 */
export function cellLines<C>(events: CalendarEvent<C>[]): { events: CalendarEvent<C>[]; more: number } {
  return { events: events.slice(0, 3), more: Math.max(events.length - 3, 0) };
}

/** 从今天起（含今天）最近的一件开着的日子；没有（没筛出可看的）时是 null。 */
export function nextEvent<C extends { birthday: string }>(
  today: string,
  data: CalendarData<C>,
  shown: CalendarKind[],
): { date: string; event: CalendarEvent<C> } | null {
  // 一年多一点，每一位的生日都轮得到；节假日和工作只在取回来的那几年里，落在外面的等明年再看。
  for (let i = 0; i <= 400; i++) {
    const date = addDays(today, i);
    const events = eventsOn(date, data, shown);
    if (events.length > 0) return { date, event: events[0] };
  }
  return null;
}

function modeKey(userId: string): string {
  return `eidolon.calendarMode.${userId}`;
}

/** 这位用户在这台设备上上次选的月 / 周；没选过是月视图。 */
export function calendarMode(userId: string): CalendarMode {
  try {
    return localStorage.getItem(modeKey(userId)) === "week" ? "week" : "month";
  } catch {
    return "month";
  }
}

/** 记下这位用户选的月 / 周；存不进去时只在这次页面里生效（由调用方自己记）。 */
export function keepCalendarMode(userId: string, mode: CalendarMode): void {
  try {
    localStorage.setItem(modeKey(userId), mode);
  } catch {
    // 存储不可用：不抛错。
  }
}
