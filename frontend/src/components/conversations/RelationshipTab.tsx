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
  const [baselineDraft, setBaselineDraft] = useState("");
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
        setBaselineDraft(found.baseline?.text ?? "");
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
  const baseline = read && read !== "loading" && read !== "failed" ? read.baseline : null;
  const changed =
    draft !== (relationship?.text ?? "") ||
    baselineDraft !== (baseline?.text ?? "");

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      const found = await saveRelationship(
        characterId,
        relationship ? draft : null,
        baselineDraft,
      );
      setRead(found);
      setDraft(found.relationship?.text ?? "");
      setBaselineDraft(found.baseline?.text ?? "");
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
      setBaselineDraft(found.baseline?.text ?? "");
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

      {relationship === null && baseline === null ? (
        <p className="text-sm text-muted-foreground">{t.empty}</p>
      ) : null}

      <div className="flex flex-col gap-1">
        <span className="text-subtitle font-medium">长期关系</span>
        <Textarea
          value={baselineDraft}
          onChange={(e) => setBaselineDraft(e.target.value)}
          rows={6}
          className="min-h-40 font-mono text-subtitle"
          aria-label="长期关系"
        />
        <p className="text-meta text-muted-foreground">
          长期关系、对制作人的评价，以及可选的相处习惯。修改不会刷新生成日期。
        </p>
      </div>

      {relationship !== null ? (
        <div className="flex flex-col gap-1">
          <span className="text-subtitle font-medium">最近关系</span>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={10}
            className="min-h-56 font-mono text-subtitle"
            aria-label={t.title}
          />
          <p className="text-meta text-muted-foreground">{t.hint}</p>
        </div>
      ) : null}

      {notice && <p className="text-body text-muted-foreground">{notice}</p>}

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={regenerate} disabled={regenerating || saving}>
          {regenerating ? t.regenerating : t.regenerate}
        </Button>
        <Button onClick={save} disabled={!changed || saving || regenerating}>
          {t.save}
        </Button>
      </div>
    </div>
  );
}
