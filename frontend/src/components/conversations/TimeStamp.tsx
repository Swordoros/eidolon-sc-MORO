/**
 * 消息区跨小时的时间条（DESIGN.md「Layout」的消息区）：和以前的日期条同一个胶囊，居中一条。
 * 今天只写 `09:12`，昨天「昨天 09:12」，今年「11月2日 09:12」，往年「2025年12月31日 22:10」；
 * 字由 `stampLabel` 拼，私聊和群聊共用。
 */
export function TimeStamp({ at, label }: { at: string; label: string }) {
  return (
    <span className="rounded-full bg-foreground/10 px-3 py-1 text-meta text-muted-foreground tabular-nums">
      <time dateTime={at}>{label}</time>
    </span>
  );
}
