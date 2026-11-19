"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowUpRight, Plus, Trash2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useLiveAccess } from "@/hooks/use-live-access";
import { useSession } from "@/lib/auth-client";

const PAGE = "mx-auto w-full max-w-3xl px-4 py-8 sm:px-8 md:py-10";
const REFRESH_MS = 30_000;

interface LiveWindow {
  id: string;
  window_start: string;
  window_end: string;
  status: "submitted" | "done" | "skipped" | "error";
  reason: string | null;
  task_id: string | null;
  clips: LiveClip[];
}

interface LiveClip {
  id: string;
  task_id: string;
  hook_title: string | null;
  ai_score: number | null;
  chat_heat: number | null;
  final_score: number | null;
}

interface LiveChannel {
  id: string;
  channel: string;
  enabled: boolean;
  chat_filter: boolean;
  min_score: number;
  chat_weight: number;
  is_live: boolean;
  live_title: string | null;
  windows: LiveWindow[];
}

type Settings = Partial<Pick<LiveChannel, "enabled" | "chat_filter" | "min_score" | "chat_weight">>;

async function errorMessage(response: Response, fallback: string) {
  try {
    const data = (await response.json()) as { detail?: unknown };
    return typeof data.detail === "string" ? data.detail : fallback;
  } catch {
    return fallback;
  }
}

function timeRange(window: LiveWindow) {
  const format = (value: string) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${format(window.window_start)}–${format(window.window_end)}`;
}

// The recorder stores fixed English reasons; translate those, show anything else (errors) as is.
function reasonText(reason: string | null, t: ReturnType<typeof useTranslations<"live">>) {
  if (!reason) return null;
  const kept = /^kept (\d+) of (\d+) clips$/.exec(reason);
  if (kept) return t("reason.kept", { kept: Number(kept[1]), total: Number(kept[2]) });
  if (reason === "no chat spike") return t("reason.noSpike");
  if (reason === "daily window cap reached") return t("reason.dailyCap");
  if (/^task [a-z_]+$/.test(reason)) return t("reason.taskFailed");
  return reason;
}

function WindowRow({ window }: { window: LiveWindow }) {
  const t = useTranslations("live");
  const reason = reasonText(window.reason, t);
  return (
    <li className="py-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="tabular-nums">{timeRange(window)}</span>
          <span className="ml-2 text-muted-foreground">{t(`status.${window.status}`)}</span>
          {reason ? <span className="ml-2 truncate text-xs text-muted-foreground">· {reason}</span> : null}
        </div>
        {window.task_id ? (
          <Link href={`/tasks/${window.task_id}`} className="inline-flex shrink-0 items-center gap-1 text-xs font-medium hover:underline">
            {t("openClips")} <ArrowUpRight className="size-3" />
          </Link>
        ) : null}
      </div>
      {window.clips.length > 0 ? (
        <ul className="mt-1 space-y-0.5 pl-3">
          {window.clips.map((clip) => (
            <li key={clip.id} className="flex items-baseline gap-2 text-xs text-muted-foreground">
              <span className="w-7 shrink-0 font-medium tabular-nums text-foreground">{clip.final_score ?? clip.ai_score}</span>
              <span className="min-w-0 truncate">{clip.hook_title}</span>
              <span className="shrink-0 tabular-nums">
                {clip.chat_heat === null
                  ? t("scores.noChat", { ai: clip.ai_score ?? 0 })
                  : t("scores.detail", { ai: clip.ai_score ?? 0, heat: clip.chat_heat })}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

// A 0-100 slider that saves when released, not on every step of a drag.
function SliderSetting({ id, label, hint, value, suffix = "", onCommit }: {
  id: string;
  label: string;
  hint: string;
  value: number;
  suffix?: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <Label id={id}>{label}</Label>
        <span className="text-sm font-medium tabular-nums">{draft}{suffix}</span>
      </div>
      <Slider
        aria-labelledby={id}
        min={0}
        max={100}
        step={1}
        value={[draft]}
        onValueChange={([next]) => setDraft(next)}
        onValueCommit={([next]) => next !== value && onCommit(next)}
      />
      <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

function ChannelCard({ channel, onChange, onRemove }: {
  channel: LiveChannel;
  onChange: (settings: Settings) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("live");

  return (
    <li className="rounded-xl border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <a href={`https://www.twitch.tv/${channel.channel}`} target="_blank" rel="noreferrer" className="font-medium hover:underline">
              {channel.channel}
            </a>
            {channel.is_live ? <Badge variant="destructive">{t("live")}</Badge> : <Badge variant="outline">{t("offline")}</Badge>}
          </div>
          {channel.is_live && channel.live_title ? (
            <p className="mt-1 truncate text-sm text-muted-foreground">{channel.live_title}</p>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t("remove", { channel: channel.channel })}>
          <Trash2 className="size-4" />
        </Button>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="flex items-center gap-2">
          <Switch id={`enabled-${channel.id}`} checked={channel.enabled} onCheckedChange={(enabled) => onChange({ enabled })} />
          <Label htmlFor={`enabled-${channel.id}`}>{t("enabled")}</Label>
        </div>
        <div className="flex items-center gap-2">
          <Switch id={`chat-${channel.id}`} checked={channel.chat_filter} onCheckedChange={(chat_filter) => onChange({ chat_filter })} />
          <Label htmlFor={`chat-${channel.id}`}>{t("chatFilter")}</Label>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{channel.chat_filter ? t("chatFilterOn") : t("chatFilterOff")}</p>

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <SliderSetting
          id={`min-score-${channel.id}`}
          label={t("minScore")}
          hint={t("minScoreHint")}
          value={channel.min_score}
          onCommit={(min_score) => onChange({ min_score })}
        />
        <SliderSetting
          id={`chat-weight-${channel.id}`}
          label={t("chatWeight")}
          hint={t("chatWeightHint", { ai: 100 - channel.chat_weight, chat: channel.chat_weight })}
          value={channel.chat_weight}
          suffix="%"
          onCommit={(chat_weight) => onChange({ chat_weight })}
        />
      </div>

      {channel.windows.length > 0 ? (
        <div className="mt-4 border-t pt-2">
          <h3 className="pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("recent")}</h3>
          <ul className="divide-y">
            {channel.windows.map((window) => <WindowRow key={window.id} window={window} />)}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

export default function LivePage() {
  const t = useTranslations("live");
  const { data: session, isPending } = useSession();
  const access = useLiveAccess(Boolean(session?.user?.id));
  const [channels, setChannels] = useState<LiveChannel[] | null>(null);
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch("/api/live/channels", { cache: "no-store" });
    if (!response.ok) throw new Error(await errorMessage(response, t("loadError")));
    setChannels(((await response.json()) as { channels: LiveChannel[] }).channels);
  }, [t]);

  useEffect(() => {
    if (!access) return;
    const refresh = () => load().catch((err: Error) => setError(err.message));
    void refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [access, load]);

  const request = async (path: string, init: RequestInit, fallback: string) => {
    setError(null);
    const response = await fetch(`/api/live/${path}`, { ...init, headers: { "Content-Type": "application/json" } });
    if (!response.ok) {
      setError(await errorMessage(response, fallback));
      return false;
    }
    await load().catch((err: Error) => setError(err.message));
    return true;
  };

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setAdding(true);
    if (await request("channels", { method: "POST", body: JSON.stringify({ channel: name.trim() }) }, t("addError"))) {
      setName("");
    }
    setAdding(false);
  };

  if (isPending || (session?.user && access === null)) {
    return (
      <main className={PAGE}>
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }

  if (!session?.user) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="mb-4 text-muted-foreground">{t("signInBody")}</p>
        <Link href="/sign-in">
          <Button>{t("signIn")}</Button>
        </Link>
      </main>
    );
  }

  if (!access) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="text-muted-foreground">{t("unavailable")}</p>
      </main>
    );
  }

  return (
    <main className={PAGE}>
      <div className="mb-8">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-muted-foreground">{t("description")}</p>
      </div>

      <form onSubmit={add} className="mb-6 flex gap-2">
        <Label htmlFor="live-channel" className="sr-only">{t("channelLabel")}</Label>
        <Input
          id="live-channel"
          placeholder={t("channelPlaceholder")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoComplete="off"
        />
        <Button type="submit" disabled={adding || !name.trim()}>
          <Plus className="size-4" /> {t("follow")}
        </Button>
      </form>

      {error ? (
        <Alert variant="destructive" className="mb-6">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {channels === null ? (
        <Skeleton className="h-40 w-full" />
      ) : channels.length === 0 ? (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <ul className="space-y-4">
          {channels.map((channel) => (
            <ChannelCard
              key={channel.id}
              channel={channel}
              onChange={(settings) => void request(`channels/${channel.id}`, { method: "PATCH", body: JSON.stringify(settings) }, t("saveError"))}
              onRemove={() => {
                if (window.confirm(t("removeConfirm", { channel: channel.channel }))) {
                  void request(`channels/${channel.id}`, { method: "DELETE" }, t("saveError"));
                }
              }}
            />
          ))}
        </ul>
      )}
    </main>
  );
}
