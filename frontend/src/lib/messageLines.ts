// 消息区的一行一行（DESIGN.md「Layout」的消息区）：跨小时处的时间条，一条消息（或她的搭话）和它在组里的位置
// （The Bubble Group Rule），改变了好感度的回复、送礼记录后面那行胶囊，以及照片用完时她推掉的
// 那条回复后面那行胶囊。

import { copy } from "@/i18n";
import type { AffinityChange } from "@/lib/affinity";
import { dayOf } from "@/lib/conversationList";
import type { Entry } from "@/lib/conversationLog";

export type Line =
  | { kind: "stamp"; key: string; at: string; label: string }
  | { kind: "entry"; entry: Entry; first: boolean; last: boolean }
  | { kind: "cue"; key: string; change: AffinityChange }
  | { kind: "photos-used-up"; key: string; retryAt: string };

/**
 * 按小时段、日期和说话的人把消息排成行：跨进另一个整点先放一条时间条；同一个人连着发、中间没有时间条、
 * 胶囊或搭话的算一组。对话事件自成一行，按它的时间参加跨小时判断，也把前后分成两组。她的搭话总是另起一组：
 * 它前面那条留着自己的时间。好感度可用时（`affinityOn`），改变了好感度的回复和送礼记录后面插一行胶囊，
 * 它也把前后分成两组；不可用时存下的变化不显示。照片用完时（这一轮收到了 `photos-used-up`），她那条回复后面
 * 再插一行胶囊，在变化胶囊之后，同样把前后分成两组。
 */
export function linesOf(entries: Entry[], now: Date, affinityOn: boolean): Line[] {
  const hours = entries.map((e) => hourOf(new Date(e.at)));
  const cueOf = (entry: Entry | undefined) =>
    affinityOn && entry && entry.kind !== "user" ? entry.affinity : null;
  const usedUpOf = (entry: Entry | undefined) => (entry?.kind === "character" ? entry.photosUsedUp : undefined);
  const splitsAfter = (entry: Entry | undefined) => cueOf(entry) !== null || usedUpOf(entry) !== undefined;
  const reachesOut = (entry: Entry | undefined) => entry?.kind === "character" && entry.reachOut === true;
  const lines: Line[] = [];
  entries.forEach((entry, i) => {
    const newStamp = i === 0 || hours[i] !== hours[i - 1];
    if (newStamp) {
      lines.push({ kind: "stamp", key: `stamp-${i}`, at: entry.at, label: stampLabel(entry.at, now) });
    }
    const previous = entries[i - 1];
    const next = entries[i + 1];
    const cue = cueOf(entry);
    const usedUp = usedUpOf(entry);
    lines.push({
      kind: "entry",
      entry,
      first: newStamp || previous.kind !== entry.kind || splitsAfter(previous) || reachesOut(entry),
      last:
        !next ||
        hours[i + 1] !== hours[i] ||
        next.kind !== entry.kind ||
        splitsAfter(entry) ||
        reachesOut(next),
    });
    if (cue) lines.push({ kind: "cue", key: `cue-${entry.key}`, change: cue });
    if (usedUp) lines.push({ kind: "photos-used-up", key: `photos-used-up-${entry.key}`, retryAt: usedUp });
  });
  return lines;
}

/** 本地墙钟上的整点段：同一小时同一串，跨进下一个整点就换。 */
export function hourOf(at: Date): string {
  return `${at.getFullYear()}-${at.getMonth()}-${at.getDate()}-${at.getHours()}`;
}

/** 时间条上的字：今天只写时刻，昨天写「昨天」，今年写月日，更早写年月日，月和日跟着本机的写法不补零。 */
export function stampLabel(at: string, now: Date): string {
  const date = new Date(at);
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  const conversation = copy.conversation;
  switch (dayOf(date, now)) {
    case "today":
      return time;
    case "yesterday":
      return conversation.stampYesterday(time);
    case "thisYear":
      return conversation.stampMonthDay(String(date.getMonth() + 1), String(date.getDate()), time);
    case "earlier":
      return conversation.stampFullDate(
        String(date.getFullYear()),
        String(date.getMonth() + 1),
        String(date.getDate()),
        time,
      );
  }
}
