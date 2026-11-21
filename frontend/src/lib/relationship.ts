// 开发者模式里的「关系」页：读、改、重生成当前角色今天的关系总结。
// 这是开发工具，普通用户看不到；关系总结本身只作为对话提示词的背景。

import { api, json } from "@/lib/api";

export type Relationship = {
  day: string;
  text: string;
  state: string;
  affinity_tier: string | null;
  edited: boolean;
  generated_at: string;
};

export type RelationshipBaseline = {
  text: string;
  relationship: string;
  opinion: string;
  habits: string[];
  edited: boolean;
  generated_at: string;
};

export type RelationshipRead = {
  relationship: Relationship | null;
  baseline: RelationshipBaseline | null;
  current_affinity: { tier: string; cooldown_until: string | null } | null;
};

export async function getRelationship(characterId: string): Promise<RelationshipRead> {
  return json(await api(`/api/dev/relationship/${encodeURIComponent(characterId)}`));
}

export async function saveRelationship(
  characterId: string,
  text: string | null,
  baseline: string | null,
): Promise<RelationshipRead> {
  return json(
    await api(`/api/dev/relationship/${encodeURIComponent(characterId)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, baseline }),
    }),
  );
}

export async function regenerateRelationship(characterId: string): Promise<RelationshipRead> {
  return json(
    await api(`/api/dev/relationship/${encodeURIComponent(characterId)}/regenerate`, {
      method: "POST",
    }),
  );
}
