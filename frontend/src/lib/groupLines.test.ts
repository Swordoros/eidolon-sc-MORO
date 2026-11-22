import { describe, expect, it } from "vitest";

import type { GroupLine, GroupTopicEntry } from "@/lib/group";
import { groupStamps } from "@/lib/groupLines";

function line(speaker: string, at: string): GroupLine {
  return { speaker, name: speaker, color: "#ffbad6", text: "…", zh: "", created_at: at, photo: null };
}

function topic(id: string, lines: GroupLine[]): GroupTopicEntry {
  return {
    topic: { id, raised_by: lines[0].speaker, text: "", created_at: lines[0].created_at, open: false },
    lines,
  };
}

/** 本地墙钟上的一个时刻，写成接口给的 ISO。 */
function local(day: number, hour: number, minute: number): string {
  return new Date(2026, 8, day, hour, minute).toISOString();
}

const NOW = new Date(2026, 8, 28, 12, 0);

describe("groupStamps", () => {
  it("跨 topic 接着上一条算：第一个话题的第一条插，同一小时不插，跨整点插", () => {
    const first = line("a", local(28, 9, 10));
    const second = line("b", local(28, 9, 55));
    const third = line("c", local(28, 10, 5));
    const fourth = line("d", local(28, 10, 40));
    const history = [topic("t1", [first, second]), topic("t2", [third, fourth])];

    const stamps = groupStamps(history, NOW);

    expect([...stamps.keys()].map((each) => each.speaker)).toEqual(["a", "c"]);
    expect(stamps.get(first)).toBe("今天 09:10");
    expect(stamps.get(third)).toBe("10:05");
  });

  it("隔了很久的第二个话题也从他自己的第一条算，不沿用上一个话题的时刻", () => {
    const history = [
      topic("t1", [line("a", local(26, 21, 10))]),
      topic("t2", [line("b", local(28, 9, 5))]),
    ];

    const stamps = groupStamps(history, NOW);

    expect([...stamps.values()]).toEqual(["9月26日 21:10", "今天 09:05"]);
  });
});
