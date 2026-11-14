import { afterEach, describe, expect, it, vi } from "vitest";

import {
  conversationDraftKey,
  DRAFT_TTL_MS,
  forgetDraft,
  groupDraftKey,
  keepDraft,
  loadDraft,
  subscribeDrafts,
} from "./composerDraft";

function storage(): Map<string, string> {
  const items = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  };
  vi.stubGlobal("localStorage", localStorage);
  return items;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("composer drafts", () => {
  it("keeps a conversation's draft apart from another's and from a group's", () => {
    storage();
    const one = conversationDraftKey("u1", "c1");
    const two = conversationDraftKey("u2", "c1");
    const group = groupDraftKey("u1", "g1");

    keepDraft(one, "还没发出去");
    keepDraft(group, "群里的草稿");

    expect(loadDraft(one)).toBe("还没发出去");
    expect(loadDraft(two)).toBe("");
    expect(loadDraft(group)).toBe("群里的草稿");
  });

  it("forgets a draft once its hour has passed, and keeps it just before", () => {
    storage();
    const key = conversationDraftKey("u1", "c1");
    keepDraft(key, "旧草稿", 1_000);

    expect(loadDraft(key, 1_000 + DRAFT_TTL_MS - 1)).toBe("旧草稿");
    expect(loadDraft(key, 1_000 + DRAFT_TTL_MS)).toBe("");
    expect(loadDraft(key, 1_000 + DRAFT_TTL_MS + 1)).toBe("");
  });

  it("starts the hour again when the draft is edited", () => {
    storage();
    const key = conversationDraftKey("u1", "c1");
    keepDraft(key, "旧草稿", 1_000);
    keepDraft(key, "改过的草稿", 1_000 + DRAFT_TTL_MS - 1);

    expect(loadDraft(key, 1_000 + DRAFT_TTL_MS + 1)).toBe("改过的草稿");
  });

  it("removes the draft when it is cleared, and ignores broken storage", () => {
    const items = storage();
    const key = conversationDraftKey("u1", "c1");
    keepDraft(key, "草稿");
    forgetDraft(key);
    expect(items.has(key)).toBe(false);

    items.set(key, "{not json");
    expect(loadDraft(key)).toBe("");
    expect(items.has(key)).toBe(false);
  });

  it("tells the list when a draft is kept or cleared", () => {
    storage();
    const told = vi.fn();
    const stop = subscribeDrafts(told);
    const key = conversationDraftKey("u1", "c1");

    keepDraft(key, "草稿");
    keepDraft(key, "");
    stop();
    keepDraft(key, "不再通知");

    expect(told).toHaveBeenCalledTimes(2);
  });

  it("does not throw when storage is unavailable", () => {
    const unavailable = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.stubGlobal("localStorage", {
      getItem: unavailable,
      setItem: unavailable,
      removeItem: unavailable,
    });

    const key = conversationDraftKey("u1", "c1");
    expect(loadDraft(key)).toBe("");
    expect(() => keepDraft(key, "草稿")).not.toThrow();
    expect(() => forgetDraft(key)).not.toThrow();
  });
});
