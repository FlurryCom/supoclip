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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { useLiveAccess } from "@/hooks/use-live-access";
import { useSession } from "@/lib/auth-client";

import { ConfirmButton } from "./confirm-button";
import { FramingSetting } from "./framing-editor";
import type { Box } from "./framing-math";
import { TrendingSection } from "./trending-section";
import { NumberField } from "./number-field";
import { PostingSection } from "./posting-section";
import { RecordingSlots } from "./recording-slots";

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
  posts: { provider: string; name: string; status: "posted" | "refused" | "error"; detail: string | null }[];
}

interface LiveChannel {
  id: string;
  platform: "twitch" | "youtube";
  channel: string;
  enabled: boolean;
  chat_filter: boolean;
  min_score: number;
  chat_weight: number;
  daily_window_cap: number;
  limit_windows: boolean;
  safe_posting: boolean;
  safe_gap_minutes: number;
  edit_kit: string;
  blur_sensitive: boolean;
  post_visibility: "default" | "unlisted" | "private";
  is_live: boolean;
  is_waiting: boolean;
  live_title: string | null;
  // Cat Clips v2 framing marked by the owner (fractions of the frame); game box alone = no webcam
  cam_box: Box | null;
  game_box: Box | null;
  windows: LiveWindow[];
}

type Settings = Partial<Pick<LiveChannel, "enabled" | "chat_filter" | "min_score" | "chat_weight" | "edit_kit" | "blur_sensitive" | "post_visibility" | "daily_window_cap" | "limit_windows" | "safe_posting" | "safe_gap_minutes" | "cam_box" | "game_box">>;

interface EditKit {
  id: string;
  name: string;
}

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
              {clip.posts.map((post) => (
                <span
                  key={`${post.provider}-${post.name}`}
                  title={post.detail ?? undefined}
                  className={post.status === "posted" ? "shrink-0 text-foreground" : "shrink-0 text-destructive"}
                >
                  · {t(`posting.status.${post.status}`, { name: post.name })}
                </span>
              ))}
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

// Owner picks whether a channel's lives are capped per day, and the number.
function WindowLimit({ channel, onChange }: { channel: LiveChannel; onChange: (settings: Settings) => void }) {
  const t = useTranslations("live");
  const [draft, setDraft] = useState(String(channel.daily_window_cap));
  useEffect(() => setDraft(String(channel.daily_window_cap)), [channel.daily_window_cap]);
  const commit = () => {
    const value = Math.round(Number(draft));
    if (Number.isFinite(value) && value >= 1 && value <= 200 && value !== channel.daily_window_cap) {
      onChange({ daily_window_cap: value });
    } else {
      setDraft(String(channel.daily_window_cap));
    }
  };
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch id={`limit-${channel.id}`} checked={channel.limit_windows} onCheckedChange={(limit_windows) => onChange({ limit_windows })} />
          <Label htmlFor={`limit-${channel.id}`}>{t("limitWindows")}</Label>
        </div>
        {channel.limit_windows ? (
          <div className="flex items-center gap-2">
            <Input
              aria-label={t("limitWindowsCount")}
              type="number"
              min={1}
              max={200}
              className="h-8 w-20"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={commit}
              onKeyDown={(event) => { if (event.key === "Enter") commit(); }}
            />
            <span className="text-sm text-muted-foreground">{t("limitWindowsPerDay")}</span>
          </div>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{channel.limit_windows ? t("limitWindowsHintOn") : t("limitWindowsHintOff")}</p>
    </div>
  );
}

function ChannelCard({ channel, kits, onChange, onRemove }: {
  channel: LiveChannel;
  kits: EditKit[];
  onChange: (settings: Settings) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("live");

  return (
    <li className="rounded-xl border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {channel.platform === "youtube" ? <SocialProviderIcon provider="youtube" className="size-4 shrink-0" /> : null}
            <a
              href={channel.platform === "youtube" ? `https://www.youtube.com/${channel.channel}` : `https://www.twitch.tv/${channel.channel}`}
              target="_blank"
              rel="noreferrer"
              className="font-medium hover:underline"
            >
              {channel.channel}
            </a>
            {channel.is_live ? (
              <Badge variant="destructive">{t("live")}</Badge>
            ) : channel.is_waiting ? (
              <Badge variant="outline" className="border-amber-500 text-amber-600" title={t("waitingHint")}>{t("waiting")}</Badge>
            ) : (
              <Badge variant="outline">{t("offline")}</Badge>
            )}
          </div>
          {channel.is_live && channel.live_title ? (
            <p className="mt-1 truncate text-sm text-muted-foreground">{channel.live_title}</p>
          ) : null}
        </div>
        <ConfirmButton variant="ghost" size="sm" question={t("removeConfirm", { channel: channel.channel })} onConfirm={onRemove} aria-label={t("remove", { channel: channel.channel })}>
          <Trash2 className="size-4" />
        </ConfirmButton>
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
      <p className="mt-2 text-xs text-muted-foreground">
        {channel.chat_filter ? t("chatFilterOn") : t("chatFilterOff")}
      </p>

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

      {kits.length > 0 ? (
        <div className="mt-5 space-y-1.5">
          <Label htmlFor={`kit-${channel.id}`}>{t("editKit")}</Label>
          <Select value={channel.edit_kit} onValueChange={(edit_kit) => onChange({ edit_kit })}>
            <SelectTrigger id={`kit-${channel.id}`} className="w-full sm:w-64"><SelectValue /></SelectTrigger>
            <SelectContent>
              {kits.map((kit) => <SelectItem key={kit.id} value={kit.id}>{kit.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">{t("editKitHint")}</p>
        </div>
      ) : null}
      {["catclips_v2", "catclips_v3"].includes(channel.edit_kit) ? <div className="mt-5"><FramingSetting channel={channel} onChange={onChange} /></div> : null}

      <div className="mt-5 space-y-1.5">
        <Label htmlFor={`vis-${channel.id}`}>{t("postVisibility")}</Label>
        <Select value={channel.post_visibility} onValueChange={(post_visibility) => onChange({ post_visibility: post_visibility as LiveChannel["post_visibility"] })}>
          <SelectTrigger id={`vis-${channel.id}`} className="w-full sm:w-64"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="default">{t("postVisibilityDefault")}</SelectItem>
            <SelectItem value="unlisted">{t("postVisibilityUnlisted")}</SelectItem>
            <SelectItem value="private">{t("postVisibilityPrivate")}</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">{t("postVisibilityHint")}</p>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Switch id={`blur-${channel.id}`} checked={channel.blur_sensitive} onCheckedChange={(blur_sensitive) => onChange({ blur_sensitive })} />
        <Label htmlFor={`blur-${channel.id}`}>{t("blurSensitive")}</Label>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("blurSensitiveHint")}</p>
      <WindowLimit channel={channel} onChange={onChange} />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch id={`safe-${channel.id}`} checked={channel.safe_posting} onCheckedChange={(safe_posting) => onChange({ safe_posting })} />
          <Label htmlFor={`safe-${channel.id}`}>{t("channelSafePosting")}</Label>
        </div>
        {channel.safe_posting ? (
          <NumberField
            value={channel.safe_gap_minutes}
            min={1}
            max={240}
            label={t("minutesBetween")}
            suffix={t("minutesBetween")}
            onCommit={(safe_gap_minutes) => onChange({ safe_gap_minutes })}
          />
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {channel.safe_posting ? t("channelSafePostingOn", { minutes: channel.safe_gap_minutes }) : t("channelSafePostingOff")}
      </p>

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

  const [kits, setKits] = useState<EditKit[]>([]);
  useEffect(() => {
    if (!access) return;
    fetch("/api/live/edit-kits", { cache: "no-store" })
      .then((response) => (response.ok ? (response.json() as Promise<EditKit[]>) : []))
      .then(setKits)
      .catch(() => setKits([]));  // no kits: the card just hides the picker
  }, [access]);

  useEffect(() => {
    if (!access) return;
    const refresh = () => load().catch((err: Error) => setError(err.message));
    void refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [access, load]);

  // Setting changes wait here until the owner presses the floating "Salvar" button.
  const [pending, setPending] = useState<Record<string, Record<string, unknown>>>({});
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(0);
  const stage = (path: string, patch: Record<string, unknown>) =>
    setPending((current) => ({ ...current, [path]: { ...current[path], ...patch } }));
  const saveAll = async () => {
    setSaving(true);
    setError(null);
    const failed: Record<string, Record<string, unknown>> = {};
    for (const [path, patch] of Object.entries(pending)) {
      const response = await fetch(`/api/live/${path}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      if (!response.ok) {
        failed[path] = patch;
        setError(await errorMessage(response, t("saveError")));
      }
    }
    setPending(failed);
    await load().catch((err: Error) => setError(err.message));
    setSavedTick((tick) => tick + 1);
    setSaving(false);
  };

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

      <RecordingSlots />

      <PostingSection pending={pending} stage={stage} savedTick={savedTick} />

      <TrendingSection onAdded={() => void load().catch((err: Error) => setError(err.message))} />

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
              channel={{ ...channel, ...pending[`channels/${channel.id}`] }}
              kits={kits}
              onChange={(settings) => stage(`channels/${channel.id}`, settings)}
              onRemove={() => void request(`channels/${channel.id}`, { method: "DELETE" }, t("saveError"))}
            />
          ))}
        </ul>
      )}

      {Object.keys(pending).length > 0 ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-24 z-50 flex justify-center md:bottom-8">
          <Button size="lg" className="pointer-events-auto rounded-full px-10 shadow-lg" disabled={saving} onClick={() => void saveAll()}>
            {saving ? t("saving") : t("save")}
          </Button>
        </div>
      ) : null}
    </main>
  );
}
