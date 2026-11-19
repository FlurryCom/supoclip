"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

// Inline two-step confirmation. window.confirm is silently blocked in some
// browsers and embedded views, which made buttons look like they did nothing.
export function ConfirmButton({ question, onConfirm, children, ...props }: {
  question: string;
  onConfirm: () => void;
  children: React.ReactNode;
} & Omit<React.ComponentProps<typeof Button>, "onClick">) {
  const t = useTranslations("live");
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return <Button {...props} onClick={() => setAsking(true)}>{children}</Button>;
  }
  return (
    <span className="flex items-center gap-2 text-sm" role="group" aria-label={question}>
      <span className="text-muted-foreground">{question}</span>
      <Button size="sm" onClick={() => { setAsking(false); onConfirm(); }}>{t("confirmYes")}</Button>
      <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>{t("confirmNo")}</Button>
    </span>
  );
}
