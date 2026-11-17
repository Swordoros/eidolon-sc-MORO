import { describe, expect, it } from "vitest";
import type { Character, Memory, MemoryScope } from "@/lib/api";
import { remainingTime, sortMemories, sourceCharacter, supersededMark, visibleTo } from "@/lib/memories";

const mano: Character = {
  id: "mano",
  name: "櫻木真乃",
  intro: "",
  avatar_url: "/api/characters/mano/avatar",
  scsp_avatar_url: null,
  color: "#ffbad6",
  birthday: "04-25",
  background_url: null,
  stand: null,
  idol_id: 1,
  short_name: null,
  name_zh: null,
  name_kana: null,
  name_romaji: null,
  unit: null,
  unit_reading: null,
  unit_order: null,
  memory_share: [],
  greeting: null,
  call_opener: null,
  call_poster_url: null,
  voxels_url: null,
};

const memory = (
  source: string,
  superseded = false,
  resolution: Memory["resolution"] = superseded ? "replaced" : null,
  scope: MemoryScope = "shared",
): Memory => ({
  id: 1,
  text: "制作人喜欢喝咖啡",
  source_character_id: source,
  source_character_name: source === "mano" ? "櫻木真乃" : null,
  created_at: "2026-09-01T12:00:00Z",
  superseded,
  resolution,
  scope,
  subject: "producer",
  kind: "short",
  status: "active",
  remaining_hours: 12,
  recall_count: 0,
  emotion: null,
  strength: 1,
});

describe("sortMemories", () => {
  it("长期新到老，然后中期新到老，最后短期新到老", () => {
    const oldLong = { ...memory("mano"), id: 1, kind: "long" as const, created_at: "2026-09-01T00:00:00Z" };
    const newLong = { ...memory("mano"), id: 2, kind: "long" as const, created_at: "2026-09-03T00:00:00Z" };
    const mid = { ...memory("mano"), id: 3, kind: "mid" as const, created_at: "2026-09-04T00:00:00Z" };
    const short = { ...memory("mano"), id: 4, kind: "short" as const, created_at: "2026-09-05T00:00:00Z" };

    expect(sortMemories([short, mid, oldLong, newLong]).map((m) => m.id)).toEqual([2, 1, 3, 4]);
  });
});

describe("remainingTime", () => {
  it("不足一小时、小时、天、无到期时间", () => {
    expect(remainingTime({ remaining_hours: 0.4 })).toBe("不足 1 小时");
    expect(remainingTime({ remaining_hours: 8.2 })).toBe("9 小时");
    expect(remainingTime({ remaining_hours: 49 })).toBe("3 天");
    expect(remainingTime({ remaining_hours: null })).toBe("—");
  });
});

describe("sourceCharacter", () => {
  it("找到记忆的来源角色，界面才能用她的颜色和头像标出来", () => {
    expect(sourceCharacter(memory("mano"), [mano])).toBe(mano);
  });

  it("来源角色已经不在配置里时没有角色", () => {
    expect(sourceCharacter(memory("hiori"), [mano])).toBeNull();
  });

  it("角色列表还没取到时也没有角色", () => {
    expect(sourceCharacter(memory("mano"), [])).toBeNull();
  });
});

describe("supersededMark", () => {
  it("被替换的记忆标出「已替换」", () => {
    expect(supersededMark(memory("mano", true))).toBe("已替换");
  });

  it("被合并的记忆标出「已合并」", () => {
    expect(supersededMark(memory("mano", true, "merged"))).toBe("已合并");
  });

  it("有效的记忆不标", () => {
    expect(supersededMark(memory("mano"))).toBeNull();
  });
});

const hiori: Character = { ...mano, id: "hiori", name: "風野灯織", unit: "illumination STARS" };
const meguru: Character = { ...mano, id: "meguru", name: "八宮めぐる", unit: "illumination STARS" };
const kogane: Character = { ...mano, id: "kogane", name: "月岡恋鐘", unit: "L'Antica" };
const hazuki: Character = {
  ...mano,
  id: "hazuki",
  name: "七草はづき",
  unit: null,
  memory_share: ["nichika", "mikoto"],
  memory_family: true,
};
const nichika: Character = {
  ...mano,
  id: "nichika",
  name: "七草にちか",
  unit: "SHHis",
  memory_share: ["hazuki"],
};
const mikoto: Character = { ...mano, id: "mikoto", name: "緋田美琴", unit: "SHHis" };
const characters = [mano, hiori, meguru, kogane, hazuki, nichika, mikoto];

describe("visibleTo", () => {
  it("她自己记下的一律能想起来，不管范围", () => {
    for (const scope of ["private", "unit", "shared"] as const) {
      expect(visibleTo(memory("hiori", false, null, scope), hiori, characters)).toBe(true);
    }
  });

  it("别人的私密记忆她想不起来，共享的可以", () => {
    expect(visibleTo(memory("hiori", false, null, "private"), mano, characters)).toBe(false);
    expect(visibleTo(memory("hiori", false, null, "shared"), mano, characters)).toBe(true);
  });

  it("群组记忆只到同一个组合，别人组合的看不到", () => {
    const unit = memory("hiori", false, null, "unit");

    expect(visibleTo(unit, meguru, characters)).toBe(true);
    expect(visibleTo(unit, kogane, characters)).toBe(false);
  });

  it("群组记忆也到 memory_share 里写的角色（叶月、日花、美琴互相可见）", () => {
    const hazukiUnit = memory("hazuki", false, null, "unit");
    const nichikaUnit = memory("nichika", false, null, "unit");

    expect(visibleTo(hazukiUnit, nichika, characters)).toBe(true);
    expect(visibleTo(hazukiUnit, mikoto, characters)).toBe(true);
    expect(visibleTo(nichikaUnit, hazuki, characters)).toBe(true);
    expect(visibleTo(hazukiUnit, hiori, characters)).toBe(false);
  });

  it("已经被取代的记忆谁都想不起来", () => {
    expect(visibleTo(memory("hiori", true, "replaced", "shared"), mano, characters)).toBe(false);
    expect(visibleTo(memory("hiori", true, "replaced", "shared"), hiori, characters)).toBe(false);
  });

  it("来源角色已经不在配置里时，群组记忆没人看得到", () => {
    expect(visibleTo(memory("gone", false, null, "unit"), mano, characters)).toBe(false);
  });
});
