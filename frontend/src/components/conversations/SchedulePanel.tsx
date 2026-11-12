"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { agendaRows, getDayAgenda, type DayAgenda } from "@/lib/scheduleAgenda";
import { herVars } from "@/theme/herVars";

const t = copy.conversation.agenda;
const REFRESH_MS = 60_000;

/**
 * 「日程安排」面板（DESIGN.md「Overlays」）：她的对话页更多菜单打开，宽屏居中的 Dialog，
 * 窄屏底部的 Drawer，都用她的颜色。读她今天的行程，按时间先后列出起床、各段安排、
 * 洗澡和睡觉；没有行程时只有一句说明。
 */
export function SchedulePanel({
  isOpen,
  onOpenChange,
  isWide,
  character,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  isWide: boolean;
  character: Character;
}) {
  const [agenda, setAgenda] = useState<DayAgenda | null | "loading" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);

  // 打开时重读；收起时留着，收起的动画里内容不消失。
  useEffect(() => {
    if (!isOpen) return;
    let gone = false;
    const read = (first: boolean) =>
      getDayAgenda(character.id).then(
        (found) => !gone && setAgenda(found),
        () => !gone && first && setAgenda("failed"),
      );
    setAgenda("loading");
    void read(true);
    // The new day's schedule may be written after midnight: replace the panel's day when
    // it is, not at 00:00, and keep showing the previous day until then.
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void read(false);
    }, REFRESH_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [isOpen, character.id, attempt]);

  const body =
    agenda === "loading" ? (
      <div className="flex justify-center p-6">
        <Spinner className="size-6 text-muted-foreground" aria-label={t.loading} />
      </div>
    ) : agenda === "failed" ? (
      <div className="flex flex-col items-center gap-3 p-6">
        <p className="text-sm text-muted-foreground">{t.failed}</p>
        <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>
          {t.retry}
        </Button>
      </div>
    ) : agenda === null ? (
      <p className="p-6 text-sm text-muted-foreground">{t.empty}</p>
    ) : (
      <div className="flex flex-col">
        {agenda.trip && (
          <p className="mb-2 rounded-md bg-muted px-3 py-2 text-meta text-muted-foreground">
            {t.trip(agenda.trip)}
          </p>
        )}
        {agendaRows(agenda).length === 0 ? (
          <p className="py-6 text-sm text-muted-foreground">{t.empty}</p>
        ) : (
          <ol className="flex flex-col">
            {agendaRows(agenda).map((row) => (
              <li
                key={row.key}
                className="grid grid-cols-[4.5rem_1fr] gap-3 border-b py-3 last:border-b-0"
              >
                <span className="text-meta tabular-nums text-muted-foreground">{row.time}</span>
                <div className="min-w-0">
                  <p className="text-body">{row.what}</p>
                  {row.where && (
                    <p className="mt-0.5 text-meta text-muted-foreground">{row.where}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    );

  // 浮层渲染到对话子树外面，在内容上再注入一次她的颜色（DESIGN.md「The Scoped Colour Rule」）。
  const style = herVars(character.color);
  if (isWide) {
    return (
      <Dialog open={isOpen} onOpenChange={onOpenChange}>
        <DialogContent style={style} className="flex max-h-[80dvh] flex-col sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
            <DialogDescription>{t.description(character.name)}</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 overflow-y-auto">{body}</div>
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={isOpen} onOpenChange={onOpenChange} showSwipeHandle>
      <DrawerContent style={style}>
        <DrawerHeader>
          <DrawerTitle>{t.title}</DrawerTitle>
          <DrawerDescription>{t.description(character.name)}</DrawerDescription>
        </DrawerHeader>
        <div className="min-h-0 overflow-y-auto px-4 pb-6">{body}</div>
      </DrawerContent>
    </Drawer>
  );
}
