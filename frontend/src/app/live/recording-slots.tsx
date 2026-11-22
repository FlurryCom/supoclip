"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";

import { NumberField } from "./number-field";

type Slots = {
  recording: number;
  waiting: number;
  limit: number;
  ceiling: number;
  share: number;
  warnings: ("cpu" | "disk")[];
  cpu_percent: number;
  disk_free_gb: number;
};

// How many lives record at once (the owner's number) and whether the machine is running tight.
export function RecordingSlots() {
  const t = useTranslations("live.slots");
  const [slots, setSlots] = useState<Slots | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    const response = await fetch("/api/live/slots", { cache: "no-store" });
    if (response.ok) setSlots(await response.json());
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const save = async (limit: number) => {
    const response = await fetch("/api/live/slots", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    });
    setError(!response.ok);
    await load();
  };

  if (!slots) return null;
  return (
    <div className="mb-5 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <span>{t("recording", { count: slots.recording })}</span>
        <NumberField value={slots.limit} min={1} max={slots.ceiling} label={t("limitLabel")} suffix={t("atOnce")} onCommit={(limit) => void save(limit)} />
        {slots.waiting > 0 ? <span className="text-amber-600 dark:text-amber-400">· {t("waiting", { count: slots.waiting })}</span> : null}
        <span className="basis-full text-xs text-muted-foreground">
          {t("hint", { ceiling: slots.ceiling })}
          {slots.share < slots.limit ? ` ${t("shared", { share: slots.share })}` : null}
        </span>
      </div>
      {error ? <p className="text-sm text-destructive">{t("saveError")}</p> : null}
      {slots.warnings.map((warning) => (
        <Alert key={warning} className="border-amber-500/50 text-amber-700 dark:text-amber-300">
          <AlertTriangle className="size-4" aria-hidden />
          <AlertDescription>
            {warning === "cpu" ? t("cpu", { percent: slots.cpu_percent }) : t("disk", { gb: slots.disk_free_gb })}
          </AlertDescription>
        </Alert>
      ))}
    </div>
  );
}
