"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, ShieldCheck, Trash2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
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
import { ConfirmButton } from "./confirm-button";
import { NumberField } from "./number-field";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import type { SocialProviderId } from "@/lib/social";

interface PostizAccount {
  id: string;
  provider: string;
  name: string;
  disabled: boolean;
  supported: boolean;
  authorized: boolean;
}

interface PostTarget {
  id: string;
  integration_id: string;
  provider: string;
  name: string;
  enabled: boolean;
  min_score: number;
  mode: "draft" | "now";
  visibility: "public" | "private";
  daily_cap: number;
  blur_sensitive: boolean;
  safe_posting: boolean;
  limit_posts: boolean;
  safe_gap_minutes: number;
  posted_today: number;
  problem: "missing" | "changed" | "disabled" | null;
}

interface PostizState {
  configured: boolean;
  allowed: boolean;
  accounts: PostizAccount[];
  targets: PostTarget[];
}

type TargetSettings = Partial<Pick<PostTarget, "enabled" | "min_score" | "mode" | "blur_sensitive" | "visibility" | "safe_posting" | "limit_posts" | "daily_cap" | "safe_gap_minutes">>;

const ICON_PROVIDERS = new Set(["youtube", "tiktok", "instagram"]);

function ProviderIcon({ provider }: { provider: string }) {
  const id = provider === "instagram-standalone" ? "instagram" : provider;
  if (!ICON_PROVIDERS.has(id)) return <span className="text-xs uppercase text-muted-foreground">{provider}</span>;
  return <SocialProviderIcon provider={id as SocialProviderId} className="size-4 shrink-0" />;
}

async function detail(response: Response, fallback: string) {
  try {
    const data = (await response.json()) as { detail?: unknown };
    return typeof data.detail === "string" ? data.detail : fallback;
  } catch {
    return fallback;
  }
}

function MinScore({ target, onCommit }: { target: PostTarget; onCommit: (value: number) => void }) {
  const t = useTranslations("live.posting");
  const [draft, setDraft] = useState(target.min_score);
  useEffect(() => setDraft(target.min_score), [target.min_score]);
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <Label id={`post-score-${target.id}`}>{t("minScore")}</Label>
        <span className="text-sm font-medium tabular-nums">{draft}</span>
      </div>
      <Slider
        aria-labelledby={`post-score-${target.id}`}
        min={0}
        max={100}
        step={1}
        value={[draft]}
        onValueChange={([next]) => setDraft(next)}
        onValueCommit={([next]) => next !== target.min_score && onCommit(next)}
      />
    </div>
  );
}

// Owner's daily post limit for this account: off means no limit; on shows the number.
function PostLimit({ target, onChange }: { target: PostTarget; onChange: (settings: TargetSettings) => void }) {
  const t = useTranslations("live.posting");
  const [draft, setDraft] = useState(String(target.daily_cap));
  useEffect(() => setDraft(String(target.daily_cap)), [target.daily_cap]);
  const commit = () => {
    const value = Math.round(Number(draft));
    if (Number.isFinite(value) && value >= 1 && value <= 100 && value !== target.daily_cap) {
      onChange({ daily_cap: value });
    } else {
      setDraft(String(target.daily_cap));
    }
  };
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2">
        <Switch id={`post-limit-${target.id}`} checked={target.limit_posts} onCheckedChange={(limit_posts) => onChange({ limit_posts })} />
        <Label htmlFor={`post-limit-${target.id}`}>{t("limitPosts")}</Label>
      </div>
      {target.limit_posts ? (
        <div className="flex items-center gap-2">
          <Input
            aria-label={t("limitPostsCount")}
            type="number"
            min={1}
            max={100}
            className="h-8 w-20"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => { if (event.key === "Enter") commit(); }}
          />
          <span className="text-sm text-muted-foreground">{t("limitPostsPerDay")}</span>
        </div>
      ) : null}
    </div>
  );
}

// Turning safe posting on is instant; turning it off first explains what the account loses.
function SafePostingSwitch({ target, onChange }: { target: PostTarget; onChange: (settings: TargetSettings) => void }) {
  const t = useTranslations("live.posting");
  const [asking, setAsking] = useState(false);
  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch
            id={`post-safe-${target.id}`}
            checked={target.safe_posting}
            onCheckedChange={(safe_posting) => (safe_posting ? onChange({ safe_posting }) : setAsking(true))}
          />
          <Label htmlFor={`post-safe-${target.id}`}>{t("safePosting")}</Label>
        </div>
        {target.safe_posting ? (
          <NumberField
            value={target.safe_gap_minutes}
            min={1}
            max={240}
            label={t("safeGapMinutes")}
            suffix={t("safeGapMinutes")}
            onCommit={(safe_gap_minutes) => onChange({ safe_gap_minutes })}
          />
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("safePostingHint", { minutes: target.safe_gap_minutes })}</p>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("safePostingOffTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("safePostingOffBody", { minutes: target.safe_gap_minutes })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("safePostingKeep")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => onChange({ safe_posting: false })}>{t("safePostingTurnOff")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function TargetCard({ target, onChange, onRevoke }: {
  target: PostTarget;
  onChange: (settings: TargetSettings) => void;
  onRevoke: () => void;
}) {
  const t = useTranslations("live.posting");
  return (
    <li className="rounded-lg border p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <ProviderIcon provider={target.provider} />
          <span className="truncate font-medium">{target.name}</span>
          <span className="truncate font-mono text-xs text-muted-foreground">{target.integration_id}</span>
        </div>
        <ConfirmButton variant="ghost" size="sm" question={t("revokeConfirm", { name: target.name })} onConfirm={onRevoke} aria-label={t("revoke", { name: target.name })}>
          <Trash2 className="size-4" />
        </ConfirmButton>
      </div>
      {target.problem ? (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {t(`problem.${target.problem}`)}
        </p>
      ) : null}
      <div className="mt-3 grid gap-4 sm:grid-cols-4 sm:items-end">
        <div className="flex items-center gap-2">
          <Switch id={`post-on-${target.id}`} checked={target.enabled} onCheckedChange={(enabled) => onChange({ enabled })} />
          <Label htmlFor={`post-on-${target.id}`}>{t("enabled")}</Label>
        </div>
        <div>
          <Label id={`post-mode-${target.id}`} className="mb-2 block">{t("mode")}</Label>
          <Select value={target.mode} onValueChange={(mode) => onChange({ mode: mode as PostTarget["mode"] })}>
            <SelectTrigger aria-labelledby={`post-mode-${target.id}`} className="h-8 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="draft">{t("modeDraft")}</SelectItem>
              <SelectItem value="now">{t("modeNow")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {target.provider.startsWith("instagram") ? null : (
          <div>
            <Label id={`post-visibility-${target.id}`} className="mb-2 block">{t("visibility")}</Label>
            <Select value={target.visibility} onValueChange={(visibility) => onChange({ visibility: visibility as PostTarget["visibility"] })}>
              <SelectTrigger aria-labelledby={`post-visibility-${target.id}`} className="h-8 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="public">{t("visibilityPublic")}</SelectItem>
                <SelectItem value="private">{t("visibilityPrivate")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        <MinScore target={target} onCommit={(min_score) => onChange({ min_score })} />
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Switch id={`post-blur-${target.id}`} checked={target.blur_sensitive} onCheckedChange={(blur_sensitive) => onChange({ blur_sensitive })} />
        <Label htmlFor={`post-blur-${target.id}`}>{t("blurSensitive")}</Label>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("blurSensitiveHint")}</p>
      <SafePostingSwitch target={target} onChange={onChange} />
      <PostLimit target={target} onChange={onChange} />
      <p className="mt-2 text-xs text-muted-foreground">
        {target.limit_posts
          ? t("today", { posted: target.posted_today, cap: target.daily_cap })
          : t("todayNoLimit", { posted: target.posted_today })}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {target.provider.startsWith("instagram") ? t("visibilityHintInstagram") : t("visibilityHint")}
      </p>
    </li>
  );
}

export function PostingSection() {
  const t = useTranslations("live.posting");
  const [state, setState] = useState<PostizState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch("/api/live/postiz/accounts", { cache: "no-store" });
    if (!response.ok) throw new Error(await detail(response, t("loadError")));
    setState((await response.json()) as PostizState);
  }, [t]);

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [load]);

  const request = async (path: string, init: RequestInit) => {
    setError(null);
    const response = await fetch(`/api/live/postiz/${path}`, { ...init, headers: { "Content-Type": "application/json" } });
    if (!response.ok) setError(await detail(response, t("saveError")));
    await load().catch((err: Error) => setError(err.message));
  };

  const candidates = state?.accounts.filter((account) => account.supported && !account.authorized) ?? [];

  return (
    <section className="mb-8 rounded-xl border bg-background p-4">
      <h2 className="flex items-center gap-2 font-medium">
        <ShieldCheck className="size-4" /> {t("title")}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("description")}</p>

      {error ? (
        <Alert variant="destructive" className="mt-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {state === null ? (
        <Skeleton className="mt-4 h-20 w-full" />
      ) : !state.configured ? (
        <p className="mt-4 text-sm text-muted-foreground">{t("notConfigured")}</p>
      ) : !state.allowed ? (
        <p className="mt-4 text-sm text-muted-foreground">{t("notAllowed")}</p>
      ) : (
        <>
          <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("authorized")}</h3>
          {state.targets.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">{t("noneAuthorized")}</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {state.targets.map((target) => (
                <TargetCard
                  key={target.id}
                  target={target}
                  onChange={(settings) => void request(`targets/${target.id}`, { method: "PATCH", body: JSON.stringify(settings) })}
                  onRevoke={() => void request(`targets/${target.id}`, { method: "DELETE" })}
                />
              ))}
            </ul>
          )}

          <h3 className="mt-5 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("available")}</h3>
          {candidates.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">{t("noneAvailable")}</p>
          ) : (
            <ul className="mt-2 divide-y rounded-lg border">
              {candidates.map((account) => (
                <li key={account.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <div className="flex min-w-0 items-center gap-2">
                    <ProviderIcon provider={account.provider} />
                    <span className="truncate">{account.name}</span>
                    <span className="truncate font-mono text-xs text-muted-foreground">{account.id}</span>
                    {account.disabled ? <Badge variant="outline">{t("disabled")}</Badge> : null}
                  </div>
                  <ConfirmButton
                    size="sm"
                    variant="outline"
                    disabled={account.disabled}
                    question={t("authorizeConfirm", { name: account.name, provider: account.provider })}
                    onConfirm={() => void request("targets", { method: "POST", body: JSON.stringify({ integration_id: account.id }) })}
                  >
                    {t("authorize")}
                  </ConfirmButton>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
