"use client";

import { useEffect, useRef, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PROSE_WIDTH } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { SettingsPane } from "@/components/settings/SettingsPane";
import { useSettingsValues } from "@/components/settings/SettingsValues";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copy } from "@/i18n";
import {
  ApiError,
  clearMemories,
  deleteMemory,
  listMemories,
  setMemoryScope,
  type Memory,
  type MemoryScope,
} from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { sourceCharacter, supersededMark, visibleTo } from "@/lib/memories";
import { cn } from "@/lib/utils";
import { ShowLessIcon, ShowMoreIcon, TrashIcon } from "@/theme/icons";

const t = copy.memories;

/** 选栏里「全部角色」那一项；其余项是角色 id。 */
const ALL_VIEWERS = "all";

const SCOPE_ORDER: MemoryScope[] = ["private", "unit", "shared"];
const SCOPE_VARIANT = {
  private: "scope-private",
  unit: "scope-unit",
  shared: "scope-shared",
} as const;

/** 点一下标签，可见范围往后轮一圈（私密 → 群组 → 共享 → 私密）。 */
function nextScope(scope: MemoryScope): MemoryScope {
  return SCOPE_ORDER[(SCOPE_ORDER.indexOf(scope) + 1) % SCOPE_ORDER.length];
}

/**
 * 设置「记忆」：所有角色共用的、关于用户的记忆，只能删，不能改。每条都标出来源角色；被取代的原地列出，淡一些并标出来。
 * 记忆是一行一行的列表（细分隔线，不套卡片）；删除一条、清空全部都要先确认，执行键是挂断红（DESIGN.md「Buttons」）。
 * 「清空」在二级页首的末端。条数一变就告诉设置的 layout，一级「记忆」的摘要跟着变。
 * 顶上「按角色查看」展开后选一位角色，只看她现在能想起来的记忆（和后台 `_visible` 同一套规矩），方便排查。
 */
export function MemoriesSettings() {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [deleting, setDeleting] = useState<Set<number>>(new Set());
  // 要删的那条。确认框关上时只把 confirmingDelete 置成 false，pendingDelete 留着，关闭动画里的文字不会闪成空白。
  const [pendingDelete, setPendingDelete] = useState<Memory | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  // 只用最后一次重新取到的列表：连着删几条时，先发出的那次晚到，会把后删的又列出来。
  const refetches = useRef(0);
  const { memoryCountChanged } = useSettingsValues();
  // 只用来给来源角色配头像；还没载入时照样列出记忆。
  const characters = useDirectory().characters ?? [];
  // 按角色查看（排查用）：展开后选一位，只看她现在能想起来的记忆；「全部」是默认。
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerId, setViewerId] = useState(ALL_VIEWERS);
  const viewer = characters.find((c) => c.id === viewerId) ?? null;
  const shown = viewer && memories ? memories.filter((m) => visibleTo(m, viewer, characters)) : memories;
  const viewers = [
    { value: ALL_VIEWERS, label: t.viewer.all },
    ...characters.map((c) => ({ value: c.id, label: c.name })),
  ];

  useEffect(() => {
    listMemories().then(setMemories, (e) => setFailure(toFailure(e)));
  }, []);

  const count = memories?.length;
  useEffect(() => {
    if (count !== undefined) memoryCountChanged(count);
  }, [count, memoryCountChanged]);

  const forget = (id: number) => setMemories((list) => list?.filter((m) => m.id !== id) ?? null);

  function askToRemove(memory: Memory) {
    setPendingDelete(memory);
    setConfirmingDelete(true);
  }

  async function remove(memory: Memory) {
    const id = memory.id;
    setConfirmingDelete(false);
    setDeleting((ids) => new Set(ids).add(id));
    setFailure(null);
    try {
      await deleteMemory(id);
      forget(id);
      // 删掉的是取代别的记忆的那条时，被它取代的会恢复，重新取一次才看得到；取不到时照旧列着。
      const refetch = ++refetches.current;
      listMemories().then((list) => refetch === refetches.current && setMemories(list), () => {});
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) forget(id); // 已经删掉了
      else setFailure(toFailure(e));
    } finally {
      setDeleting((ids) => {
        const rest = new Set(ids);
        rest.delete(id);
        return rest;
      });
    }
  }

  async function clear() {
    setClearing(true);
    setFailure(null);
    try {
      await clearMemories();
      setMemories([]);
      setConfirmingClear(false);
    } catch (e) {
      setFailure(toFailure(e));
      setConfirmingClear(false);
    } finally {
      setClearing(false);
    }
  }

  async function changeScope(memory: Memory) {
    const scope = nextScope(memory.scope);
    setFailure(null);
    try {
      await setMemoryScope(memory.id, scope);
      setMemories((list) => list?.map((m) => (m.id === memory.id ? { ...m, scope } : m)) ?? null);
    } catch (e) {
      setFailure(toFailure(e));
    }
  }

  const hasMemories = !!memories && memories.length > 0;

  return (
    <SettingsPane
      category="memories"
      end={
        hasMemories && (
          <Button variant="ghost" size="sm" onClick={() => setConfirmingClear(true)} disabled={clearing}>
            {t.clear}
          </Button>
        )
      }
    >
      <div className="flex flex-1 flex-col gap-4">
        <p className={cn(PROSE_WIDTH, "text-body text-muted-foreground")}>{t.intro}</p>

        {hasMemories && (
          <div className="flex flex-col items-start gap-2">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={viewerOpen}
              onClick={() => setViewerOpen(!viewerOpen)}
              className="max-md:min-h-11"
            >
              {viewerOpen ? <ShowLessIcon data-icon="inline-start" /> : <ShowMoreIcon data-icon="inline-start" />}
              {t.viewer.open}
              {viewer && <span className="font-normal text-muted-foreground">· {viewer.name}</span>}
            </Button>
            {viewerOpen && (
              <div className="flex w-full flex-col gap-2">
                <p className={cn(PROSE_WIDTH, "text-subtitle text-muted-foreground")}>{t.viewer.hint}</p>
                <Select
                  items={viewers}
                  value={viewerId}
                  onValueChange={(next) => setViewerId(String(next ?? ALL_VIEWERS))}
                >
                  <SelectTrigger aria-label={t.viewer.label} className="w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {viewers.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        )}

        {failure && <FailureNotice failure={failure} />}

        {memories === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : shown && shown.length > 0 ? (
          <ul className="flex flex-col divide-y">
            {shown.map((memory) => {
              const source = sourceCharacter(memory, characters);
              const family = (source?.memory_share.length ?? 0) > 0;
              const name = memory.source_character_name ?? t.goneCharacter;
              const created = new Date(memory.created_at);
              const mark = supersededMark(memory);
              const busy = deleting.has(memory.id);
              return (
                <li key={memory.id} className="flex items-start gap-3 py-4">
                  <div className="pt-0.5">
                    <MarkedAvatar name={name} src={source?.avatar_url} size="sm" />
                  </div>
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <p className={cn("text-body break-words", mark && "text-muted-foreground line-through")}>{memory.text}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{t.subjectName[memory.subject]}</Badge>
                      {memory.subject === "producer" ? (
                        <button
                          type="button"
                          aria-label={t.changeScope(t.scopeName(nextScope(memory.scope), family))}
                          title={t.changeScope(t.scopeName(nextScope(memory.scope), family))}
                          onClick={() => changeScope(memory)}
                          disabled={busy || clearing}
                          className="shrink-0 rounded-4xl focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                        >
                          <Badge variant={SCOPE_VARIANT[memory.scope]}>{t.scopeName(memory.scope, family)}</Badge>
                        </button>
                      ) : (
                        <Badge variant="scope-private">{t.scopeName("private")}</Badge>
                      )}
                      {mark && <Badge variant="secondary">{mark}</Badge>}
                      {memory.emotion && <Badge variant="outline">{memory.emotion}</Badge>}
                      <Badge variant="outline">{t.strengthValue(memory.strength)}</Badge>
                      <span className="text-subtitle text-muted-foreground">
                        {t.toldTo(name)} · {t.date(created.getFullYear(), created.getMonth() + 1, created.getDate())}
                      </span>
                    </div>
                  </div>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t.removeLabel(memory.text)}
                          disabled={busy || clearing}
                          onClick={() => askToRemove(memory)}
                        />
                      }
                    >
                      {busy ? <Spinner /> : <TrashIcon />}
                    </TooltipTrigger>
                    <TooltipContent>{t.remove}</TooltipContent>
                  </Tooltip>
                </li>
              );
            })}
          </ul>
        ) : viewer ? (
          <p className="text-body text-muted-foreground">{t.viewer.empty(viewer.name)}</p>
        ) : (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>{t.empty}</EmptyTitle>
            </EmptyHeader>
          </Empty>
        )}
      </div>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.removeTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete ? t.removeDescription(pendingDelete.text) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{copy.common.cancel}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => pendingDelete && remove(pendingDelete)}>
              {t.remove}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmingClear} onOpenChange={(open) => !clearing && setConfirmingClear(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.clearTitle}</AlertDialogTitle>
            <AlertDialogDescription>{t.clearDescription(memories?.length ?? 0)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>{copy.common.cancel}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={clearing} onClick={clear}>
              {clearing && <Spinner data-icon="inline-start" />}
              {t.clearConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsPane>
  );
}
