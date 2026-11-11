"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { copy } from "@/i18n";
import {
  getRelationship,
  regenerateRelationship,
  saveRelationship,
  type RelationshipRead,
} from "@/lib/relationship";

const t = copy.conversation.devRelationship;

/**
 * 开发者「提示词」窗口里的「关系」标签页：读当前角色今天的关系总结，文本可编辑，
 * 保存后作为提示词使用；也能重新生成。关系总结不是用户界面。
 */
export function RelationshipTab({
  characterId,
  isOpen,
}: {
  characterId: string;
  isOpen: boolean;
}) {
  const [read, setRead] = useState<RelationshipRead | null | "loading" | "failed">("loading");
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    setRead("loading");
    setNotice(null);
    getRelationship(characterId).then(
      (found) => {
        setRead(found);
        setDraft(found.relationship?.text ?? "");
      },
      () => setRead("failed"),
    );
  };

  useEffect(() => {
    if (isOpen) load();
    // characterId and isOpen identify the tab; load is intentionally not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [characterId, isOpen]);

  const relationship = read && read !== "loading" && read !== "failed" ? read.relationship : null;
  const changed = draft !== (relationship?.text ?? "");

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      const found = await saveRelationship(characterId, draft);
      setRead(found);
      setDraft(found.relationship?.text ?? "");
      setNotice(t.saved);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : t.saveFailed);
    } finally {
      setSaving(false);
    }
  }

  async function regenerate() {
    setRegenerating(true);
    setNotice(null);
    try {
      const found = await regenerateRelationship(characterId);
      setRead(found);
      setDraft(found.relationship?.text ?? "");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : t.regenerateFailed);
    } finally {
      setRegenerating(false);
    }
  }

  if (read === null || read === "loading") {
    return (
      <div className="flex justify-center p-6">
        <Spinner className="size-6 text-muted-foreground" aria-label={t.title} />
      </div>
    );
  }
  if (read === "failed") {
    return (
      <div className="flex flex-col items-center gap-3 p-6">
        <p className="text-sm text-muted-foreground">{t.regenerateFailed}</p>
        <Button variant="outline" onClick={load}>
          {t.retry}
        </Button>
      </div>
    );
  }

  const affinity = read.current_affinity;
  const tiers = copy.conversation.affinity.tiers;
  const tier = affinity ? tiers[affinity.tier as keyof typeof tiers] : null;

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-muted-foreground">
        {tier && <span>{t.affinity(tier)}</span>}
        {affinity?.cooldown_until && <span>{t.cooling}</span>}
        {relationship && <span>{t.generatedAt(new Date(relationship.generated_at).toLocaleString("zh-CN"))}</span>}
      </div>

      {relationship === null ? (
        <p className="py-6 text-sm text-muted-foreground">{t.empty}</p>
      ) : (
        <>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={12}
            className="min-h-64 font-mono text-subtitle"
            aria-label={t.title}
          />
          <p className="text-meta text-muted-foreground">{t.hint}</p>
        </>
      )}

      {notice && <p className="text-body text-muted-foreground">{notice}</p>}

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={regenerate} disabled={regenerating || saving}>
          {regenerating ? t.regenerating : t.regenerate}
        </Button>
        <Button onClick={save} disabled={!relationship || !changed || saving || regenerating}>
          {t.save}
        </Button>
      </div>
    </div>
  );
}
