"use client";

import { useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/i18n";
import { switchCrossChat, switchPlove } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.settings.chat;

/** 设置「聊天设置」：允许角色和制作人恋爱，以及群聊与私聊的上下文互通。 */
export function ChatSettings() {
  const { account, accountChanged } = useDirectory();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  if (!account) return null;
  const on = account.cross_chat;
  const plove = account.allow_plove;

  const changePlove = (next: boolean) => {
    setFailure(null);
    setSaving(true);
    accountChanged((current) => ({ ...current, allow_plove: next }));
    switchPlove(next).then(
      () => setSaving(false),
      (error) => {
        accountChanged((current) => ({ ...current, allow_plove: plove }));
        setFailure(toFailure(error));
        setSaving(false);
      },
    );
  };

  const change = (next: boolean) => {
    setFailure(null);
    setSaving(true);
    accountChanged((current) => ({ ...current, cross_chat: next }));
    switchCrossChat(next).then(
      () => setSaving(false),
      (error) => {
        accountChanged((current) => ({ ...current, cross_chat: on }));
        setFailure(toFailure(error));
        setSaving(false);
      },
    );
  };

  return (
    <>
      <SettingsSection>
        <FieldGroup>
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor={`${id}-plove`}>{t.plove}</FieldLabel>
              <FieldDescription>{t.ploveHint}</FieldDescription>
            </FieldContent>
            <Switch
              id={`${id}-plove`}
              checked={plove}
              disabled={saving}
              onCheckedChange={changePlove}
            />
          </Field>
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor={`${id}-cross-chat`}>{t.crossChat}</FieldLabel>
              <FieldDescription>{t.crossChatHint}</FieldDescription>
            </FieldContent>
            <Switch
              id={`${id}-cross-chat`}
              checked={on}
              disabled={saving}
              onCheckedChange={change}
            />
          </Field>
          {failure && <FailureNotice failure={failure} />}
        </FieldGroup>
      </SettingsSection>
    </>
  );
}
