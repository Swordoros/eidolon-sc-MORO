"use client";

import { useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/i18n";
import { setNsfw } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

const t = copy.settings.nsfw;

/**
 * 设置「成人内容」（GLOSSARY.md「成人内容」）：对话、通话语音、照片三个开关，各自默认关，
 * 只影响自己，且只对 18 岁及以上的角色生效。存在服务器上，一拨马上存。
 */
export function NsfwSettings() {
  const { account, accountChanged } = useDirectory();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  if (!account) return null;

  const change = (key: "conversation" | "voice" | "photo", next: boolean) => {
    setFailure(null);
    setSaving(true);
    const field = key === "conversation" ? "nsfw" : key === "voice" ? "nsfw_voice" : "nsfw_photo";
    accountChanged((current) => ({ ...current, [field]: next }));
    setNsfw({ [key]: next }).then(
      () => setSaving(false),
      (e) => {
        accountChanged((current) => ({ ...current, [field]: account[field] }));
        setFailure(toFailure(e));
        setSaving(false);
      },
    );
  };

  const switches: { key: "conversation" | "voice" | "photo"; label: string; hint: string }[] = [
    { key: "conversation", label: t.conversation, hint: t.conversationHint },
    { key: "voice", label: t.voice, hint: t.voiceHint },
    { key: "photo", label: t.photo, hint: t.photoHint },
  ];

  return (
    <SettingsSection>
      <FieldGroup>
        {switches.map(({ key, label, hint }) => (
          <Field key={key} orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor={`${id}-${key}`}>{label}</FieldLabel>
              <FieldDescription>{hint}</FieldDescription>
            </FieldContent>
            <Switch
              id={`${id}-${key}`}
              checked={key === "conversation" ? account.nsfw : key === "voice" ? account.nsfw_voice : account.nsfw_photo}
              disabled={saving}
              onCheckedChange={(next) => change(key, next)}
            />
          </Field>
        ))}
        {failure && <FailureNotice failure={failure} />}
      </FieldGroup>
    </SettingsSection>
  );
}
