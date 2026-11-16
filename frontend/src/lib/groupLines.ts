// 群聊消息区的时间条：每个整点小时段一条，跨 topic 也接着上一条算（DESIGN.md「Layout」的消息区）。
// 文字由 `stampLabel` 拼，和私聊同一套；返回每条线前面要放的那条时间条，没轮到的线没有。

import type { GroupLine, GroupTopicEntry } from "@/lib/group";
import { hourOf, stampLabel } from "@/lib/messageLines";

/** 群聊里每条线前面要不要放时间条：这条线是它所在的那个小时段的第一条时给出字，其余没有。 */
export function groupStamps(history: GroupTopicEntry[], now: Date): Map<GroupLine, string> {
  const stamps = new Map<GroupLine, string>();
  let previous: string | null = null;
  for (const { lines } of history) {
    for (const line of lines) {
      const hour = hourOf(new Date(line.created_at));
      if (hour !== previous) stamps.set(line, stampLabel(line.created_at, now));
      previous = hour;
    }
  }
  return stamps;
}
