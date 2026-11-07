"use client";

import { useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/i18n";
import { setDev } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.settings.dev;

/**
 * 设置「开发」（GLOSSARY.md「开发」）：提示词调试的开关，默认关。打开后对话页更多菜单里多出
 * 「上一次生成对话中的全套提示词」，能看到模型实际收到的完整提示词。存在服务器上，一拨马上存。
 */
export function DevSettings() {
  const { account, accountChanged } = useDirectory();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  if (!account) return null;
  const on = account.dev;

  const change = (next: boolean) => {
    setFailure(null);
    setSaving(true);
    accountChanged((current) => ({ ...current, dev: next }));
    setDev(next).then(
      () => setSaving(false),
      (e) => {
        accountChanged((current) => ({ ...current, dev: on }));
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
            <FieldDescription>{t.hint}</FieldDescription>
          </FieldContent>
          <Switch id={`${id}-on`} checked={on} disabled={saving} onCheckedChange={change} />
        </Field>
        {failure && <FailureNotice failure={failure} />}
      </FieldGroup>
    </SettingsSection>
  );
}
