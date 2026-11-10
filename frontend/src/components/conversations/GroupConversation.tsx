"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
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
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Message, MessageContent, MessageHeader } from "@/components/ui/message";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import {
  dissolveGroup,
  getGroup,
  lastGroupPrompt,
  renameGroup,
  sayInGroup,
  setGroupMembers,
  type GroupDetail,
  type GroupLine,
  type GroupTopic,
} from "@/lib/group";
import { formatTimestamp } from "@/lib/timestamp";
import { cn } from "@/lib/utils";
import {
  BackIcon,
  DevIcon,
  EditRecordIcon,
  MoreIcon,
  SendIcon,
  TrashIcon,
  UsersIcon,
} from "@/theme/icons";

import { useDirectory } from "./ConversationDirectory";

const t = copy.play.group;

/** 有对话在聊时多久重新读一次；没在聊时慢一些。台词是后台一句一句写的。 */
const POLL_UNDER_WAY_MS = 3_000;
const POLL_IDLE_MS = 15_000;

/**
 * 一个群的页面（`/conversation/group/<id>`），和私聊同一套界面：页首是群名、成员头像和人数，
 * 下面是全部历史（每个话题一条分隔，台词按时间排），底部是输入栏。改群名、拉人和解散都在
 * 三个点的更多菜单里（解散确认后回到对话列表）。台词在后台一句一句生成，所以这里隔几秒重读
 * 一次，用户随时能在中间插话。
 */
export function GroupConversation({
  groupId,
  onBack,
}: {
  groupId: string;
  onBack?: () => void;
}) {
  const router = useRouter();
  const { account, characters } = useDirectory();
  const isFullScreen = !useMediaQuery("(min-width: 768px)", true);
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<null | "sending" | "renaming" | "picking" | "dissolving">(
    null,
  );
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [picking, setPicking] = useState(false);
  const [rosterOpen, setRosterOpen] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [askingDissolve, setAskingDissolve] = useState(false);
  const [devPromptOpen, setDevPromptOpen] = useState(false);
  const [devPrompt, setDevPrompt] = useState<string | null | undefined>(undefined);
  const byId = useMemo(
    () => new Map((characters ?? []).map((c) => [c.id, c])),
    [characters],
  );
  const open = group?.topic?.open ?? false;

  const load = useCallback(() => {
    getGroup(groupId).then(
      (state) => {
        setGroup(state);
        setFailed(false);
      },
      () => setFailed(true),
    );
  }, [groupId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(load, open ? POLL_UNDER_WAY_MS : POLL_IDLE_MS);
    return () => window.clearInterval(id);
  }, [load, open]);

  const run = useCallback(
    async (
      what: "sending" | "renaming" | "picking" | "dissolving",
      work: () => Promise<GroupDetail>,
    ) => {
      setBusy(what);
      try {
        setGroup(await work());
        setFailed(false);
      } catch {
        setFailed(true);
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const send = useCallback(() => {
    const value = draft.trim();
    if (!value || !group?.on || busy) return;
    setDraft("");
    void run("sending", () => sayInGroup(groupId, value));
  }, [busy, draft, group?.on, groupId, run]);

  const openRename = useCallback(() => {
    setNewName(group?.name ?? "");
    setRenaming(true);
  }, [group?.name]);

  const confirmRename = useCallback(() => {
    const value = newName.trim();
    if (!value || busy) return;
    setRenaming(false);
    void run("renaming", () => renameGroup(groupId, value));
  }, [busy, groupId, newName, run]);

  const openPicker = useCallback(() => {
    setChosen(group?.members.map((m) => m.id) ?? []);
    setPicking(true);
  }, [group?.members]);

  const openDevPrompt = useCallback(() => {
    setDevPromptOpen(true);
    setDevPrompt(undefined);
    lastGroupPrompt(groupId).then(setDevPrompt, () => setDevPrompt(null));
  }, [groupId]);

  const confirmMembers = useCallback(() => {
    if (chosen.length === 0 || busy) return;
    setPicking(false);
    void run("picking", () => setGroupMembers(groupId, chosen));
  }, [busy, chosen, groupId, run]);

  const confirmDissolve = useCallback(() => {
    if (busy) return;
    setAskingDissolve(false);
    setBusy("dissolving");
    void dissolveGroup(groupId).then(
      () => {
        router.replace("/conversations");
      },
      () => {
        setBusy(null);
        setFailed(true);
      },
    );
  }, [busy, groupId, router]);

  const back = () => (onBack ? onBack() : router.push("/conversations"));

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center gap-1 px-2">
        {isFullScreen && (
          <Button
            variant="ghost"
            size="icon-lg"
            className="max-md:size-11"
            onClick={back}
            aria-label="返回"
          >
            <BackIcon className="size-6" />
          </Button>
        )}
        {group && group.members.length > 0 && (
          <button
            type="button"
            onClick={() => setRosterOpen(true)}
            className="flex shrink-0 items-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            title={t.roster}
            aria-label={t.roster}
          >
            {group.members.slice(0, 4).map((member, index) => (
              <span key={member.id} className={cn("rounded-full", index > 0 && "-ml-2")}>
                <MarkedAvatar
                  name={member.name}
                  src={byId.get(member.id)?.avatar_url ?? null}
                  size="sm"
                />
              </span>
            ))}
          </button>
        )}
        <div className="flex min-w-0 flex-1 flex-col px-2">
          <h1 className="truncate text-header">{group?.name ?? t.title}</h1>
          <p className="truncate text-meta text-muted-foreground">
            {group ? t.status(group.members.length) : ""}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon-lg"
                className="max-md:size-11"
                aria-label={copy.conversation.menu}
              />
            }
          >
            <MoreIcon className="size-5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={busy !== null || !group} onClick={openRename}>
              <EditRecordIcon aria-hidden />
              {t.rename}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={busy !== null} onClick={openPicker}>
              <UsersIcon aria-hidden />
              {t.addMembers}
            </DropdownMenuItem>
            {account?.dev && (
              <DropdownMenuItem onClick={openDevPrompt}>
                <DevIcon aria-hidden />
                {copy.conversation.devPrompt}
              </DropdownMenuItem>
            )}
            {group?.on && (
              <DropdownMenuItem
                variant="destructive"
                disabled={busy !== null}
                onClick={() => setAskingDissolve(true)}
              >
                <TrashIcon aria-hidden />
                {t.dissolve}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <div className="min-h-0 flex-1 bg-background">
        {failed && (
          <div className="px-3 pt-3">
            <FailureNotice failure={{ message: t.failed, signedOut: false }} />
          </div>
        )}
        {!group ? (
          <div className="flex h-full items-center justify-center">
            <Spinner />
          </div>
        ) : group.history.length === 0 ? (
          <div className="flex h-full items-center justify-center px-3">
            <Empty>
              <EmptyHeader>
                <EmptyTitle>{group.name}</EmptyTitle>
                <EmptyDescription>{t.noHistory}</EmptyDescription>
              </EmptyHeader>
            </Empty>
          </div>
        ) : (
          <MessageScrollerProvider autoScroll defaultScrollPosition="end">
            <MessageScroller>
              <MessageScrollerViewport>
                <MessageScrollerContent className="gap-4 px-3 pt-4 pb-4">
                  {group.history.map((entry) => (
                    <MessageScrollerItem key={entry.topic.id}>
                      <div className="mx-auto w-full max-w-3xl">
                        <TopicBlock entry={entry} byId={byId} />
                      </div>
                    </MessageScrollerItem>
                  ))}
                </MessageScrollerContent>
              </MessageScrollerViewport>
            </MessageScroller>
          </MessageScrollerProvider>
        )}
      </div>

      {group && !group.on && (
        <p className="px-4 py-2 text-center text-meta text-muted-foreground">{t.dissolved}</p>
      )}

      <div className="shrink-0 border-t bg-background px-3 py-2">
        <div className="flex items-end gap-2">
          <Textarea
            variant="composer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
            placeholder={group?.on ? t.placeholder : t.dissolved}
            disabled={!group?.on || busy !== null}
            rows={1}
            className="flex-1"
          />
          <Button
            type="submit"
            variant="her"
            size="icon-lg"
            onClick={send}
            disabled={!group?.on || busy !== null || !draft.trim()}
            aria-label={t.send}
            onMouseDown={(e) => e.preventDefault()}
          >
            <SendIcon aria-hidden className="size-5" />
          </Button>
        </div>
        {(busy === "sending" || open) && (
          <p className="mt-1 text-meta text-muted-foreground">
            {busy === "sending" ? t.sending : t.raising}
          </p>
        )}
      </div>

      <Dialog open={rosterOpen} onOpenChange={setRosterOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.roster}</DialogTitle>
            <DialogDescription>
              {group?.name ?? t.title} · {group ? t.status(group.members.length) : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto py-2">
            {(group?.members ?? []).map((member) => (
              <div key={member.id} className="flex items-center gap-2 px-2 py-1">
                <MarkedAvatar
                  name={member.name}
                  src={byId.get(member.id)?.avatar_url ?? null}
                  size="sm"
                />
                <span className="truncate text-body">{member.name}</span>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button onClick={() => setRosterOpen(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.rename}</DialogTitle>
          </DialogHeader>
          <Input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                confirmRename();
              }
            }}
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenaming(false)} disabled={busy !== null}>
              取消
            </Button>
            <Button onClick={confirmRename} disabled={busy !== null || !newName.trim()}>
              改名
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={picking} onOpenChange={setPicking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.createTitle}</DialogTitle>
            <DialogDescription>{t.createHint}</DialogDescription>
          </DialogHeader>
          <div className="grid max-h-80 grid-cols-2 gap-2 overflow-y-auto py-2">
            {(characters ?? []).map((character) => {
              const picked = chosen.includes(character.id);
              return (
                <button
                  key={character.id}
                  type="button"
                  aria-pressed={picked}
                  onClick={() =>
                    setChosen((current) =>
                      current.includes(character.id)
                        ? current.filter((id) => id !== character.id)
                        : [...current, character.id],
                    )
                  }
                  className={cn(
                    "flex items-center gap-2 rounded-lg border px-2 py-2 text-left text-body transition-colors",
                    picked ? "border-primary bg-muted" : "border-border",
                  )}
                >
                  <MarkedAvatar
                    name={character.name}
                    src={character.avatar_url}
                    size="sm"
                  />
                  <span className="truncate">{character.name}</span>
                </button>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPicking(false)} disabled={busy !== null}>
              取消
            </Button>
            <Button onClick={confirmMembers} disabled={busy !== null || chosen.length === 0}>
              {busy === "picking" ? t.creating : t.create}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={devPromptOpen} onOpenChange={setDevPromptOpen}>
        <DialogContent className="max-w-[min(48rem,92vw)]">
          <DialogHeader>
            <DialogTitle>{copy.conversation.devPrompt}</DialogTitle>
            <DialogDescription className="sr-only">
              {copy.conversation.devPrompt}
            </DialogDescription>
          </DialogHeader>
          {devPrompt === undefined ? (
            <p className="text-body text-muted-foreground">…</p>
          ) : (
            <pre className="max-h-[64vh] overflow-auto whitespace-pre-wrap rounded-2xl bg-muted p-4 text-subtitle">
              {devPrompt ?? copy.conversation.devPromptNone}
            </pre>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={askingDissolve} onOpenChange={setAskingDissolve}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.dissolveTitle}</AlertDialogTitle>
            <AlertDialogDescription>{t.dissolveDescription}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={confirmDissolve}
              disabled={busy !== null}
            >
              {busy === "dissolving" ? t.dissolving : t.dissolveConfirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 一个话题：一条「新话题」分隔，然后是它的台词（和私聊同一套气泡：同一个人连着说并成一个气泡，带时刻）。 */
function TopicBlock({
  entry,
  byId,
}: {
  entry: { topic: GroupTopic; lines: GroupLine[] };
  byId: Map<string, Character>;
}) {
  const lines = entry.lines;
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-meta text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        <span>{t.newTopic}</span>
        <span>{formatTimestamp(entry.topic.created_at, { format: "auto", now: new Date() })}</span>
        <span className="h-px flex-1 bg-border" />
      </div>
      {lines.map((line, index) => {
        const mine = line.color === null;
        const first = index === 0 || lines[index - 1].speaker !== line.speaker;
        const last = index === lines.length - 1 || lines[index + 1].speaker !== line.speaker;
        const time = (
          <time className="shrink-0 pb-0.5 text-meta text-muted-foreground tabular-nums">{clock(line.created_at)}</time>
        );
        if (mine) {
          return (
            <Message key={`${entry.topic.id}-${index}`} align="end">
              <div className="flex min-w-0 flex-1 items-end justify-end gap-1.5">
                {last && time}
                <Bubble variant="producer" align="end" joinsPrevious={!first} joinsNext={!last}>
                  <BubbleContent>
                    <p className="text-body whitespace-pre-wrap">{line.text}</p>
                  </BubbleContent>
                </Bubble>
              </div>
            </Message>
          );
        }
        return (
          <Message key={`${entry.topic.id}-${index}`} align="start">
            {first ? (
              <MarkedAvatar name={line.name} src={byId.get(line.speaker)?.avatar_url ?? null} size="bubble" />
            ) : (
              <span aria-hidden className="w-bubble-avatar shrink-0" />
            )}
            <MessageContent className="gap-1">
              {first && <MessageHeader className="px-1">{line.name}</MessageHeader>}
              <div className="flex min-w-0 items-end gap-1.5">
                <Bubble variant="her" joinsPrevious={!first} joinsNext={!last}>
                  <BubbleContent>
                    <div className="flex flex-col gap-0.5">
                      <p className="text-japanese-line whitespace-pre-wrap">{line.text}</p>
                      {line.zh && (
                        <p className="text-subtitle whitespace-pre-wrap text-muted-foreground">{line.zh}</p>
                      )}
                    </div>
                  </BubbleContent>
                </Bubble>
                {last && time}
              </div>
            </MessageContent>
          </Message>
        );
      })}
    </section>
  );
}

/** 气泡旁的时刻，写成 8:49（和私聊里今天的时间一样）。 */
function clock(at: string): string {
  const d = new Date(at);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getHours()}:${two(d.getMinutes())}`;
}
