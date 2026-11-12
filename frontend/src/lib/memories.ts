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
