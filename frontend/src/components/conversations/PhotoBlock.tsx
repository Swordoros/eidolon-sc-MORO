"use client";

import { useEffect, useState } from "react";

import { ShellLink } from "@/components/app/ShellLink";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import {
  getPhoto,
  photoImageUrl,
  retryPhoto,
  type Photo,
  type PhotoStatus,
} from "@/lib/api";
import { photoFailureNote } from "@/lib/photoFailure";
import { settingsHref } from "@/lib/settingsCategory";
import { PhotoFailedIcon, PhotoIcon } from "@/theme/icons";

import { PhotoViewer } from "./PhotoViewer";

const t = copy.conversation.photo;

/**
 * 她那条回复的第一块：她发来的照片（GLOSSARY.md「照片」，DESIGN.md「Chat bubbles · 照片」）。
 * 宽 192px，按 832×1216 等比；生成中是同样大小的占位，到了直接换上，位置不跳。点开整屏放大。
 * 没发出来时换成同宽的一块矮说明：「照片没发出来」和原因，可以重新请求生成；
 * 自带的生图密钥没能用时多一行「去设置自带密钥」。
 */
export function PhotoBlock({ photo }: { photo: Photo }) {
  const [viewing, setViewing] = useState(false);
  const [local, setLocal] = useState<PhotoStatus | null>(null);
  const [retrying, setRetrying] = useState(false);
  const current = local ?? photo;

  useEffect(() => {
    if (!local || local.status !== "pending") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      try {
        const next = await getPhoto(photo.id);
        if (stopped) return;
        setLocal(next);
        if (next.status === "pending") timer = setTimeout(check, 1000);
      } catch {
        if (!stopped) setLocal({ status: "failed", reason: "service_error" });
      }
    };
    void check();
    return () => {
      stopped = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [local?.status, photo.id]);

  if (current.status === "pending") {
    return (
      <div
        role="status"
        aria-label={t.pending}
        className="flex aspect-[832/1216] w-48 flex-col items-center justify-center gap-2 rounded-bubble bg-foreground/8 text-muted-foreground"
      >
        <PhotoIcon aria-hidden className="size-6 motion-safe:animate-pulse" />
        <Spinner className="size-4" />
      </div>
    );
  }
  if (current.status === "failed") {
    const onRetry = async () => {
      setRetrying(true);
      try {
        setLocal(await retryPhoto(photo.id));
      } catch {
        try {
          setLocal(await getPhoto(photo.id));
        } catch {
          setLocal({ status: "failed", reason: "service_error" });
        }
      } finally {
        setRetrying(false);
      }
    };
    return (
      <PhotoNotSent
        reason={current.reason}
        retrying={retrying}
        onRetry={() => void onRetry()}
      />
    );
  }
  const src = photoImageUrl(photo.id);
  return (
    <>
      <button
        type="button"
        aria-label={t.open}
        onClick={() => setViewing(true)}
        className="w-48 overflow-hidden rounded-bubble outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <img src={src} alt={t.sent} width={832} height={1216} className="aspect-[832/1216] w-full object-cover" />
      </button>
      <PhotoViewer id={photo.id} open={viewing} onOpenChange={setViewing} />
    </>
  );
}

function PhotoNotSent({
  reason,
  retrying,
  onRetry,
}: {
  reason: string | null;
  retrying: boolean;
  onRetry: () => void;
}) {
  const note = photoFailureNote(reason);
  return (
    <div role="note" className="flex w-48 gap-2 rounded-bubble bg-foreground/5 px-4 py-3 text-muted-foreground">
      <PhotoFailedIcon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="flex min-w-0 flex-col gap-0.5 text-sm">
        <p>{t.failed}</p>
        <p className="text-meta">{note.reason}</p>
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="mt-1 inline-flex w-fit items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-meta font-medium text-foreground outline-none hover:bg-muted/80 focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
        >
          {retrying && <Spinner className="size-3" />}
          {retrying ? t.retrying : t.retry}
        </button>
        {note.toOwnKeys && (
          <p className="text-meta">
            <ShellLink href={settingsHref("own-keys")} isStandalone>
              {t.toOwnKeys}
            </ShellLink>
          </p>
        )}
      </div>
    </div>
  );
}
