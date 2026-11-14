// 输入框里还没发出去的草稿：只存在这台设备上（localStorage），单聊按对话分开，群聊按群分开。
// 最后一次编辑起一小时后过期；读不出、存不进去（隐私模式等）时不抛错，草稿只在当前页面内存里。

const KEY_PREFIX = "eidolon.composerDraft.";

/** 草稿从最后一次编辑起保留多久。 */
export const DRAFT_TTL_MS = 60 * 60 * 1000;

let revision = 0;
const listeners = new Set<() => void>();

type StoredDraft = { text: string; at: number };

/** Tells useSyncExternalStore that a draft changed, in this tab or another. */
export function draftsRevision(): number {
  return revision;
}

export function subscribeDrafts(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key?.startsWith(KEY_PREFIX)) changed();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

function changed(): void {
  revision++;
  for (const listener of listeners) listener();
}

export function conversationDraftKey(userId: string, conversationId: string): string {
  return `${KEY_PREFIX}${userId}.${conversationId}`;
}

export function groupDraftKey(userId: string, groupId: string): string {
  return `${KEY_PREFIX}group.${userId}.${groupId}`;
}

function forget(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // 存储不可用：本来也什么都没存下。
  }
}

/** 这个键下还没过期的草稿；没有、过期或读不出时是空字符串。 */
export function loadDraft(key: string, now = Date.now()): string {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return "";
    const stored = JSON.parse(raw) as Partial<StoredDraft>;
    if (typeof stored.text !== "string" || typeof stored.at !== "number") {
      forget(key);
      return "";
    }
    if (now - stored.at >= DRAFT_TTL_MS) {
      forget(key);
      return "";
    }
    return stored.text;
  } catch {
    forget(key);
    return "";
  }
}

/** 存下这个键的草稿；空字符串就是删掉。存不进去时只在当前页面内存里。 */
export function keepDraft(key: string, text: string, now = Date.now()): void {
  if (text === "") {
    forget(key);
    changed();
    return;
  }
  try {
    const stored: StoredDraft = { text, at: now };
    localStorage.setItem(key, JSON.stringify(stored));
  } catch {
    // 存储不可用：这次存不下，改动只在这次页面里生效。
  }
  changed();
}

/** 删掉这个键的草稿。 */
export function forgetDraft(key: string): void {
  forget(key);
  changed();
}
