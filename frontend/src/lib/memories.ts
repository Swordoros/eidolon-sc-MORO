// 记忆页：每条记忆都标注来源角色（GLOSSARY.md「来源角色」），被取代的记忆标出「已被取代」（ADR-0014）。

import { copy } from "@/i18n";
import type { Character, Memory } from "@/lib/api";

/** 这条记忆的来源角色；她已经不在配置里（或角色列表还没取到）时为 null。 */
export function sourceCharacter(memory: Memory, characters: readonly Character[]): Character | null {
  return characters.find((c) => c.id === memory.source_character_id) ?? null;
}

/** 被替换或合并的记忆旁边的标记；有效的记忆没有标记。 */
export function supersededMark(memory: Memory): string | null {
  if (memory.resolution === "merged") return copy.memories.merged;
  if (memory.resolution === "replaced") return copy.memories.replaced;
  return memory.superseded ? copy.memories.replaced : null;
}

/**
 * 这条记忆 `viewer` 现在能不能想起来：她自己的一律算，别人的只在共享时算，群组的还要
 * 来源角色的组合和她相同、或在来源角色的 `memory_share` 里；已被取代的谁都想不起。
 * 和后台 `Memories._visible` 同一套规矩，记忆页按角色查看时用。
 */
export function visibleTo(memory: Memory, viewer: Character, characters: readonly Character[]): boolean {
  if (memory.superseded) return false;
  if (memory.source_character_id === viewer.id) return true;
  if (memory.scope === "shared") return true;
  if (memory.scope !== "unit") return false;
  const source = sourceCharacter(memory, characters);
  if (source === null) return false;
  if (source.memory_share.includes(viewer.id)) return true;
  return viewer.unit !== null && viewer.unit === source.unit;
}
