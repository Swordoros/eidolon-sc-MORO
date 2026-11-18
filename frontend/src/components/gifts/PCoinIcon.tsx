import type { SVGProps } from "react";

import { cn } from "@/lib/utils";

/**
 * P 币图标：几何 P，不依赖字体基线，圆圈和 P 的包围盒中心重合。
 * 颜色用当前主题的 primary，与页面文字保持同一套色。
 */
export function PCoinIcon({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden
      className={cn("size-4 shrink-0", className)}
      {...props}
    >
      <circle cx="12" cy="12" r="10" className="fill-primary" />
      <path
        d="M8.5 5.8h5.1a4.2 4.2 0 0 1 0 8.4h-3.2v4H8.5V5.8Zm2.1 2v4.6h3a2.3 2.3 0 0 0 0-4.6h-3Z"
        className="fill-primary-foreground"
      />
    </svg>
  );
}

/** 金额和 P 币图标；读屏仍读「P-Coin 45」。 */
export function PCoinAmount({
  amount,
  className,
  iconClassName,
  amountClassName,
}: {
  amount: number;
  className?: string;
  iconClassName?: string;
  amountClassName?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1 tabular-nums", className)}>
      <PCoinIcon className={iconClassName} />
      <span className="sr-only">P-Coin </span>
      <span className={cn("text-right", amountClassName)}>{amount}</span>
    </span>
  );
}
