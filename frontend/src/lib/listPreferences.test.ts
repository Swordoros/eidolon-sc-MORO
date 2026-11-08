import { afterEach, describe, expect, it, vi } from "vitest";

import {
  hide,
  keepListPreferences,
  keepPinnedGroups,
  listPreferences,
  NO_PREFERENCES,
  pin,
  pinnedGroups,
  pinGroup,
  setShown,
  show,
  showAll,
  unpin,
  unpinGroup,
} from "@/lib/listPreferences";

// 配置里现有的角色。
const KNOWN = ["mano", "hiori", "meguru", "kogane", "mamimi"];

/** 这个浏览器的 localStorage，放在内存里。 */
function storage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  });
  return items;
}

afterEach(() => vi.unstubAllGlobals());

describe("这台设备上的隐藏和置顶", () => {
  it("按用户分开存，存下的读回来一样", () => {
    storage();

    keepListPreferences("u1", pin(hide(NO_PREFERENCES, ["mamimi"]), "mano"));
    keepListPreferences("u2", hide(NO_PREFERENCES, ["hiori"]));

    expect(listPreferences("u1", KNOWN)).toEqual({ hidden: ["mamimi"], pinned: ["mano"] });
    expect(listPreferences("u2", KNOWN)).toEqual({ hidden: ["hiori"], pinned: [] });
    expect(listPreferences("u3", KNOWN)).toEqual(NO_PREFERENCES);
  });

  it("配置里已经不存在的角色读的时候忽略", () => {
    storage({ "eidolon.listPreferences.u1": JSON.stringify({ hidden: ["gone", "hiori"], pinned: ["left", "mano"] }) });

    expect(listPreferences("u1", KNOWN)).toEqual({ hidden: ["hiori"], pinned: ["mano"] });
  });

  it("数据坏了当作什么都没整理", () => {
    storage({
      "eidolon.listPreferences.u1": "{not json",
      "eidolon.listPreferences.u2": JSON.stringify(["mano"]),
      "eidolon.listPreferences.u3": JSON.stringify({ hidden: "mano", pinned: [42, "hiori"] }),
    });

    expect(listPreferences("u1", KNOWN)).toEqual(NO_PREFERENCES);
    expect(listPreferences("u2", KNOWN)).toEqual(NO_PREFERENCES);
    expect(listPreferences("u3", KNOWN)).toEqual({ hidden: [], pinned: ["hiori"] });
  });

  it("存储用不了时不抛错，读到的是什么都没整理", () => {
    const unreachable = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: unreachable, setItem: unreachable, removeItem: unreachable });

    expect(() => keepListPreferences("u1", hide(NO_PREFERENCES, ["mano"]))).not.toThrow();
    expect(listPreferences("u1", KNOWN)).toEqual(NO_PREFERENCES);
  });

});

describe("整理列表", () => {
  it("隐藏一位置顶的角色，她的置顶一并取消", () => {
    const pinned = pin(NO_PREFERENCES, "mano");

    expect(hide(pinned, ["mano"])).toEqual({ hidden: ["mano"], pinned: [] });
  });

  it("置顶一位隐藏的角色，她同时恢复显示", () => {
    const hidden = hide(NO_PREFERENCES, ["mano", "hiori"]);

    expect(pin(hidden, "mano")).toEqual({ hidden: ["hiori"], pinned: ["mano"] });
  });

  it("setShown 按勾没勾显示或隐藏", () => {
    expect(setShown(NO_PREFERENCES, ["mano"], false)).toEqual({ hidden: ["mano"], pinned: [] });
    expect(setShown(hide(NO_PREFERENCES, ["mano"]), ["mano"], true)).toEqual(NO_PREFERENCES);
  });

  it("显示和取消置顶只动那一位", () => {
    const prefs = pin(pin(hide(NO_PREFERENCES, ["meguru", "kogane"]), "mano"), "hiori");

    expect(show(prefs, ["meguru"])).toEqual({ hidden: ["kogane"], pinned: ["mano", "hiori"] });
    expect(unpin(prefs, "mano")).toEqual({ hidden: ["meguru", "kogane"], pinned: ["hiori"] });
  });

  it("整组隐藏、整组显示", () => {
    const prefs = pin(hide(NO_PREFERENCES, ["mano"]), "meguru");
    const illumination = ["mano", "hiori", "meguru"];

    const hidden = hide(prefs, illumination);
    expect(hidden).toEqual({ hidden: ["mano", "hiori", "meguru"], pinned: [] });
    expect(show(hidden, illumination)).toEqual(NO_PREFERENCES);
  });

  it("重复隐藏、重复置顶不会多记", () => {
    expect(hide(hide(NO_PREFERENCES, ["mano"]), ["mano"])).toEqual({ hidden: ["mano"], pinned: [] });
    expect(pin(pin(NO_PREFERENCES, "mano"), "mano")).toEqual({ hidden: [], pinned: ["mano"] });
  });

  it("「全部显示」只清空隐藏，置顶不变", () => {
    const prefs = pin(hide(NO_PREFERENCES, ["hiori", "kogane"]), "mano");

    expect(showAll(prefs)).toEqual({ hidden: [], pinned: ["mano"] });
  });
});

describe("群聊的置顶", () => {
  it("和角色的置顶分开存，置顶和取消置顶往返一样", () => {
    storage();

    keepPinnedGroups("u1", pinGroup(pinnedGroups("u1"), "g-1"));
    keepPinnedGroups("u1", pinGroup(pinnedGroups("u1"), "g-2"));
    keepPinnedGroups("u1", unpinGroup(pinGroup(["g-1", "g-2"], "g-3"), "g-1"));

    expect(pinnedGroups("u1")).toEqual(["g-2", "g-3"]);
    expect(pinnedGroups("u2")).toEqual([]);
  });

  it("数据坏了当作没有置顶", () => {
    storage({ "eidolon.listPreferences.groups.u1": "{not json" });

    expect(pinnedGroups("u1")).toEqual([]);
  });
});
