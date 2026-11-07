"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { copy } from "@/i18n";
import { setVisitOutreachMode, switchReachOuts } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.settings.reachOuts;

const visitOptions = [
  { value: "off", label: "关闭" },
  { value: "natural", label: "自然偶遇" },
  { value: "tonight", label: "今晚想听你先说" },
];
type VisitMode = "off" | "natural" | "tonight";

/**
 * 设置「搭话」（GLOSSARY.md「搭话」）：一个开关，关了所有角色都不再搭话。存在服务器上，一拨马上存，别的设备上也生效；
 * 存不上就拨回去，照原话说明。值在导航外壳持有的账号里，一级「搭话」的摘要跟着变。
 * 部署者没开搭话时账号里这一项为空，没有这一类：直接打开这个网址时回到设置页。
 */
export function ReachOutsSettings() {
  const { account, accountChanged } = useDirectory();
  const router = useRouter();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [savingVisit, setSavingVisit] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const on = account?.reach_outs ?? null;

  useEffect(() => {
    if (account && on === null) router.replace("/settings");
  }, [account, on, router]);

  if (!account || on === null) return null;

  const visitMode = account.visit_outreach_mode ?? (account.visit_outreach ? "natural" : "off");
  const changeVisit = (next: VisitMode) => {
    setFailure(null);
    setSavingVisit(true);
    accountChanged((current) => ({ ...current, visit_outreach: next !== "off", visit_outreach_mode: next }));
    setVisitOutreachMode(next).catch((error) => {
      accountChanged((current) => ({ ...current, visit_outreach: account.visit_outreach, visit_outreach_mode: account.visit_outreach_mode }));
      setFailure(toFailure(error));
    }).finally(() => setSavingVisit(false));
  };

  const change = (next: boolean) => {
    setFailure(null);
    setSaving(true);
    accountChanged((current) => ({ ...current, reach_outs: next }));
    switchReachOuts(next).then(
      () => setSaving(false),
      (e) => {
        accountChanged((current) => ({ ...current, reach_outs: on }));
        setFailure(toFailure(e));
        setSaving(false);
      },
    );
  };

  return (
    <SettingsSection>
      <FieldGroup>
        <Field orientation="horizontal">
          <FieldContent>
            <FieldLabel htmlFor={`${id}-on`}>{t.on}</FieldLabel>
          </FieldContent>
          <Switch id={`${id}-on`} checked={on} disabled={saving} onCheckedChange={change} />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${id}-visit`}>夜间相遇</FieldLabel>
          <Select items={visitOptions} value={visitMode} disabled={savingVisit}
            onValueChange={(value) => value && changeVisit(value as VisitMode)}>
            <SelectTrigger id={`${id}-visit`} className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              {visitOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>
            {visitMode === "off" ? "角色不会在你进入聊天时主动开口。" : visitMode === "natural"
              ? "夜间进入聊天时，角色偶尔会先与你说话。"
              : "今晚进入聊天、稍作停留后，让角色先开口。下一晚恢复「自然偶遇」。"}
          </FieldDescription>
          {visitMode !== "off" && <FieldDescription>
            按个人资料时区，于 20:00 至次日 05:00 生效。请先与角色完成至少一轮对话。
            {visitMode === "natural"
              ? "自然偶遇受关系、近期交流与每晚准备次数限制。"
              : "今晚模式跳过机会抽选、30 分钟近期交流限制与自然模式的每晚准备次数上限。"}
            通话中、角色正在回复或已有未回应的搭话时不会再次开口；启用好感度后，需要达到信任或更高关系。
          </FieldDescription>}
        </Field>
        {failure && <FailureNotice failure={failure} />}
      </FieldGroup>
    </SettingsSection>
  );
}
