"use client";

import { useEffect, useState } from "react";

import { GiftTile } from "@/components/gifts/GiftTile";
import { PCoinAmount } from "@/components/gifts/PCoinIcon";
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
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import {
  buyGift,
  sellGift,
  type Exchange,
  type GiftInfo,
  type Owned,
} from "@/lib/gifts";

const t = copy.gifts.exchange;

type Mode = "sell" | "buy";

type Chosen = { gift: GiftInfo; max: number; price: number };

/**
 * 礼物兑换入口：显示 P-Coin 余额，打开面板后可以出售背包里的礼物，或用 P-Coin 兑换目录里的礼物。
 * 选好数量和项目后还要在确认框里再确认一次。
 */
export function ExchangeDesk({
  pCoins,
  backpack,
  catalog,
  onDone,
  onClose,
}: {
  pCoins: number;
  backpack: Owned[];
  catalog: GiftInfo[];
  onDone: (exchange: Exchange) => void;
  onClose: () => void;
}) {
  const [isOpen, setOpen] = useState(false);

  return (
    <section aria-label={t.title} className="flex flex-wrap items-center gap-3 rounded-2xl border px-5 py-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-header">{t.title}</p>
        <p className="flex items-center gap-2 text-subtitle text-muted-foreground">
          {t.balanceLabel}
          <PCoinAmount amount={pCoins} className="text-header text-foreground" iconClassName="size-5" />
        </p>
      </div>
      <Button variant="outline" onClick={() => setOpen(true)}>
        {t.sell} / {t.buy}
      </Button>
      <ExchangeDialog
        isOpen={isOpen}
        pCoins={pCoins}
        backpack={backpack}
        catalog={catalog}
        onDone={onDone}
        onClose={() => {
          setOpen(false);
          onClose();
        }}
      />
    </section>
  );
}

function ExchangeDialog({
  isOpen,
  pCoins,
  backpack,
  catalog,
  onDone,
  onClose,
}: {
  isOpen: boolean;
  pCoins: number;
  backpack: Owned[];
  catalog: GiftInfo[];
  onDone: (exchange: Exchange) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>("sell");
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [count, setCount] = useState(1);
  const [isConfirming, setConfirming] = useState(false);
  const [isSending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setChosen(null);
      setCount(1);
      setFailed(false);
      setConfirming(false);
    }
  }, [isOpen]);

  const held = new Map(backpack.map(({ gift_id, count: n }) => [gift_id, n]));
  const entries = catalog
    .filter((gift) => (mode === "sell" ? (held.get(gift.id) ?? 0) > 0 : !gift.retired))
    .sort((a, b) => a.sell_price - b.sell_price);

  function choose(gift: GiftInfo) {
    const max = mode === "sell" ? (held.get(gift.id) ?? 0) : 99;
    setChosen({ gift, max, price: mode === "sell" ? gift.sell_price : gift.buy_price });
    setCount(1);
    setFailed(false);
  }

  function switchMode(next: Mode) {
    setMode(next);
    setChosen(null);
    setCount(1);
    setFailed(false);
  }

  const total = chosen ? chosen.price * count : 0;
  const cannotAfford = mode === "buy" && total > pCoins;
  const canSubmit = chosen !== null && count <= chosen.max && !cannotAfford && !isSending;

  async function submit() {
    if (!chosen) return;
    setSending(true);
    setFailed(false);
    try {
      const exchange = mode === "sell"
        ? await sellGift(chosen.gift.id, count)
        : await buyGift(chosen.gift.id, count);
      onDone(exchange);
      setConfirming(false);
      setChosen(null);
      setCount(1);
      setFailed(false);
    } catch {
      setFailed(true);
      setConfirming(false);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <Dialog open={isOpen} onOpenChange={(open) => !open && !isSending && onClose()}>
        <DialogContent className="flex max-h-[80vh] flex-col gap-4 sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
            <DialogDescription className="flex items-center gap-2">
              {t.balanceLabel}
              <PCoinAmount amount={pCoins} className="text-foreground" />
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2">
            <Button
              variant={mode === "sell" ? "secondary" : "outline"}
              onClick={() => switchMode("sell")}
            >
              {t.sell}
            </Button>
            <Button
              variant={mode === "buy" ? "secondary" : "outline"}
              onClick={() => switchMode("buy")}
            >
              {t.buy}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">{mode === "sell" ? t.sellHint : t.buyHint}</p>
          {entries.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {mode === "sell" ? t.empty : t.buyEmpty}
            </p>
          ) : (
            <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
              {entries.map((gift) => {
                const n = held.get(gift.id) ?? 0;
                return (
                  <li key={gift.id}>
                    <button
                      type="button"
                      onClick={() => choose(gift)}
                      className={`flex w-full items-center gap-3 rounded-xl p-2 text-left outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 ${
                        chosen?.gift.id === gift.id ? "bg-muted" : ""
                      }`}
                    >
                      <GiftTile gift={gift} size="small" />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm">{gift.name}</span>
                        <span className="text-meta text-muted-foreground">
                          {copy.gifts.rarity[gift.rarity]}
                          {mode === "sell" ? ` · ${t.owned(n)}` : ""}
                          {gift.retired ? ` · ${t.retired}` : ""}
                        </span>
                      </span>
                      <span className="text-sm tabular-nums">
                        <PCoinAmount
                          amount={mode === "sell" ? gift.sell_price : gift.buy_price}
                          iconClassName="size-3.5"
                          amountClassName="min-w-6"
                        />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {chosen && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl bg-muted p-3">
              <span className="text-sm">{chosen.gift.name}</span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="icon-sm"
                  aria-label={t.less}
                  disabled={count <= 1 || isSending}
                  onClick={() => setCount((n) => Math.max(1, n - 1))}
                >
                  −
                </Button>
                <span className="w-8 text-center text-sm tabular-nums">{count}</span>
                <Button
                  variant="outline"
                  size="icon-sm"
                  aria-label={t.more}
                  disabled={count >= chosen.max || isSending}
                  onClick={() => setCount((n) => Math.min(chosen.max, n + 1))}
                >
                  +
                </Button>
              </div>
              <span className="ml-auto text-sm tabular-nums">
                <PCoinAmount amount={total} iconClassName="size-3.5" />
              </span>
              {cannotAfford && <span className="text-meta text-destructive">{t.notEnough}</span>}
              <Button className="w-full" disabled={!canSubmit} onClick={() => setConfirming(true)}>
                {mode === "sell" ? t.sell : t.buy}
              </Button>
              {failed && <p className="w-full text-sm text-destructive">{t.failed}</p>}
            </div>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={isConfirming} onOpenChange={(open) => !isSending && setConfirming(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {mode === "sell" ? t.confirmSell.title : t.confirmBuy.title}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {mode === "sell" ? (
                <>
                  出售 {chosen?.gift.name ?? ""} ×{count}，获得{" "}
                  <PCoinAmount amount={total} />
                  。
                </>
              ) : (
                <>
                  花费 <PCoinAmount amount={total} />，兑换 {chosen?.gift.name ?? ""} ×{count}。
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isSending}>{copy.common.cancel}</AlertDialogCancel>
            <AlertDialogAction disabled={isSending} onClick={submit}>
              {isSending && <Spinner data-icon="inline-start" />}
              {mode === "sell" ? t.confirmSell.action : t.confirmBuy.action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
