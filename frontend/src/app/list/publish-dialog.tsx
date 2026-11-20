"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

type Target = { id: string; name: string; provider: string; enabled: boolean; problem: string | null };
type Result = { queued: number; already: number; not_live: number };

async function detail(response: Response, fallback: string) {
  const data = await response.json().catch(() => null);
  return typeof data?.detail === "string" ? data.detail : fallback;
}

// Sends every live clip of the chosen generations to the checked Postiz accounts (any score).
export function PublishDialog({ open, onOpenChange, taskIds, onDone }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taskIds: string[];
  onDone: (message: string, tone: "success" | "error") => void;
}) {
  const t = useTranslations("library.publish");
  const [targets, setTargets] = useState<Target[] | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTargets(null);
    setError(null);
    fetch("/api/live/postiz/accounts", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await detail(response, t("loadError")));
        const data = (await response.json()) as { targets: Target[] };
        const usable = data.targets.filter((target) => target.enabled && !target.problem);
        setTargets(usable);
        setChecked(usable.map((target) => target.id));
      })
      .catch((err: Error) => setError(err.message));
  }, [open, t]);

  const toggle = (id: string, on: boolean) =>
    setChecked((current) => (on ? [...current, id] : current.filter((value) => value !== id)));

  const publish = async () => {
    setSending(true);
    setError(null);
    try {
      const response = await fetch("/api/live/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_ids: taskIds, target_ids: checked }),
      });
      if (!response.ok) throw new Error(await detail(response, t("sendError")));
      const result = (await response.json()) as Result;
      const parts = [t("queued", { count: result.queued })];
      if (result.already > 0) parts.push(t("already", { count: result.already }));
      if (result.not_live > 0) parts.push(t("notLive", { count: result.not_live }));
      onDone(parts.join(" · "), result.queued > 0 ? "success" : "error");
      onOpenChange(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={(value) => !sending && onOpenChange(value)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("title", { count: taskIds.length })}</AlertDialogTitle>
          <AlertDialogDescription>{t("body")}</AlertDialogDescription>
        </AlertDialogHeader>

        {targets === null && !error && <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />}
        {targets?.length === 0 && <p className="text-sm text-muted-foreground">{t("noAccounts")}</p>}
        {targets && targets.length > 0 && (
          <div className="space-y-2">
            {targets.map((target) => (
              <label key={target.id} className="flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-sm">
                <Checkbox
                  checked={checked.includes(target.id)}
                  onCheckedChange={(value) => toggle(target.id, value === true)}
                />
                <span className="font-medium">{target.name}</span>
                <span className="ml-auto text-xs capitalize text-muted-foreground">{target.provider}</span>
              </label>
            ))}
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={sending}>{t("cancel")}</AlertDialogCancel>
          <Button onClick={() => void publish()} disabled={sending || checked.length === 0}>
            {sending && <Loader2 className="h-4 w-4 animate-spin" />}
            {t("confirm")}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
