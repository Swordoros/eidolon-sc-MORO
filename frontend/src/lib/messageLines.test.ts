import { describe, expect, it } from "vitest";

import type { AffinityChange } from "@/lib/affinity";
import type { Entry } from "@/lib/conversationLog";
import { linesOf } from "@/lib/messageLines";

const UP: AffinityChange = { up: true, tier_changed: false, tier: "trust", cooldown: false, thawed: false };

function user(key: string, at = "2026-09-28T01:00:00Z"): Entry {
  return { kind: "user", key, text: "在吗", at };
}

function her(key: string, affinity: AffinityChange | null = null, at = "2026-09-28T01:00:01Z"): Entry {
  return { kind: "character", key, segments: [{ ja: "はい", zh: "好的" }], pending: false, at, affinity, photo: null };
}

function hungUp(key: string, at: string): Entry {
  return { kind: "event", key, event: "hung_up", at, gift: null, affinity: null };
}

const NOW = new Date("2026-09-28T12:00:00Z");

describe("linesOf", () => {
  it("好感度不可用时，存下的变化不出胶囊", () => {
    const lines = linesOf([user("m1"), her("m2", UP)], NOW, false);

    expect(lines.some((line) => line.kind === "cue")).toBe(false);
  });

  it("变化的胶囊挂在改变了好感度的那条回复后面；换了一个小时段先放时间条；同一个人连着发、中间没有时间条或胶囊的算一组", () => {
    const lines = linesOf(
      [
        user("m1", "2026-09-27T01:00:00Z"),
        user("m2", "2026-09-28T01:00:00Z"),
        user("m3", "2026-09-28T01:00:02Z"),
        her("m4", UP, "2026-09-28T01:00:03Z"),
        her("m5", null, "2026-09-28T01:00:04Z"),
      ],
      NOW,
      true,
    );

    const groups = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(groups).toEqual(["stamp", "m1<>", "stamp", "m2<", "m3>", "m4<>", "cue", "m5<>"]);
    expect(lines.find((line) => line.kind === "cue")).toEqual({ kind: "cue", key: "cue-m4", change: UP });
  });

  it("她的搭话总是另起一组：前一条她的话留着自己的时间，搭话重新带头像和名字", () => {
    const reachOut: Entry = {
      kind: "character",
      key: "r1",
      segments: [{ ja: "ねえ", zh: "呐" }],
      pending: false,
      at: "2026-09-28T09:00:00Z",
      affinity: null,
      photo: null,
      reachOut: true,
    };

    const lines = linesOf([user("m1"), her("m2"), reachOut], NOW, true);

    expect(lines.flatMap((line) => (line.kind === "entry" ? [[line.entry.key, line.first, line.last]] : []))).toEqual([
      ["m1", true, true],
      ["m2", true, true],
      ["r1", true, true],
    ]);
  });

  it("改变了好感度的送礼记录后面也挂胶囊", () => {
    const gift: Entry = { kind: "event", key: "e1", event: "gift", at: "2026-09-28T01:00:02Z", gift: "蛋糕", affinity: UP };

    const lines = linesOf([user("m1"), gift, her("m2", null, "2026-09-28T01:00:03Z")], NOW, true);

    expect(lines.map((line) => (line.kind === "entry" ? line.entry.key : line.kind))).toEqual([
      "stamp",
      "m1",
      "e1",
      "cue",
      "m2",
    ]);
  });

  it("照片用完的胶囊挂在她推掉的那条回复后面（在变化胶囊之后），也把前后分成两组", () => {
    const usedUp = (key: string, affinity: AffinityChange | null = null): Entry => {
      const entry = her(key, affinity);
      return entry.kind === "character" ? { ...entry, photosUsedUp: "2026-09-28T02:00:00Z" } : entry;
    };
    const lines = linesOf([user("m1"), usedUp("m2"), her("m3"), usedUp("m4", UP), her("m5")], NOW, true);

    const groups = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(groups).toEqual(["stamp", "m1<>", "m2<>", "photos-used-up", "m3<", "m4>", "cue", "photos-used-up", "m5<>"]);
    expect(lines.find((line) => line.kind === "photos-used-up")).toEqual({
      kind: "photos-used-up",
      key: "photos-used-up-m2",
      retryAt: "2026-09-28T02:00:00Z",
    });
  });

  it("对话事件把前后分成两组，按它的时间参加跨小时判断", () => {
    const lines = linesOf(
      [
        her("m1", null, "2026-09-27T01:00:00Z"),
        hungUp("e1", "2026-09-27T01:00:05Z"),
        her("m2", null, "2026-09-27T01:00:06Z"),
        hungUp("e2", "2026-09-28T01:00:00Z"),
        user("m3", "2026-09-28T01:00:01Z"),
      ],
      NOW,
      true,
    );

    const shown = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(shown).toEqual(["stamp", "m1<>", "e1<>", "m2<>", "stamp", "e2<>", "m3<>"]);
  });

  it("时间条今天只写时刻，昨天、今年、往年带上日期", () => {
    const lines = linesOf(
      [
        user("a", local(2025, 12, 31, 22, 10)),
        user("b", local(2026, 9, 2, 8, 5)),
        user("c", local(2026, 9, 27, 23, 40)),
        user("d", local(2026, 9, 28, 9, 12)),
        user("e", local(2026, 9, 28, 10, 5)),
      ],
      new Date(2026, 8, 28, 12, 0),
      false,
    );

    expect(stampsOf(lines)).toEqual([
      "2025年12月31日 22:10",
      "9月2日 08:05",
      "昨天 23:40",
      "今天 09:12",
      "10:05",
    ]);
  });

  it("昨天跨年也写昨天：不用年份换一句更长的日期", () => {
    const lines = linesOf([user("a", local(2025, 12, 31, 23, 50))], new Date(2026, 0, 1, 0, 30), false);

    expect(stampsOf(lines)).toEqual(["昨天 23:50"]);
  });

  it("按整点每小时一条：同一小时不插，跨整点插，气泡也跟着断开", () => {
    const lines = linesOf(
      [
        her("a", null, local(2026, 9, 28, 8, 59)),
        her("b", null, local(2026, 9, 28, 9, 1)),
        her("c", null, local(2026, 9, 28, 9, 30)),
      ],
      new Date(2026, 8, 28, 12, 0),
      false,
    );

    const shown = lines.flatMap((line) =>
      line.kind === "stamp"
        ? [line.label]
        : line.kind === "entry"
          ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`]
          : [],
    );
    expect(shown).toEqual(["今天 08:59", "a<>", "09:01", "b<", "c>"]);
  });
});

/** 本地墙钟上的一个时刻，写成接口给的 ISO。 */
function local(year: number, month: number, day: number, hour: number, minute: number): string {
  return new Date(year, month - 1, day, hour, minute).toISOString();
}

function stampsOf(lines: ReturnType<typeof linesOf>): string[] {
  return lines.flatMap((line) => (line.kind === "stamp" ? [line.label] : []));
}
