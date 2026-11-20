"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useLiveAccess } from "@/hooks/use-live-access";
import { useSession } from "@/lib/auth-client";

type Video = { id: string; title: string; duration: number | null; thumbnail: string | null; short: boolean; too_long: boolean; taken: boolean };
type Listing = { platform: string; channel: string; items: Video[] };
type Target = { id: string; name: string; provider: string; enabled: boolean; problem: string | null };
type Kit = { id: string; name: string };
type Item = { id: string; title: string; platform: string; status: string; reason: string | null; task_id: string | null };

const PAGE = "mx-auto w-full max-w-[1100px] px-4 py-6 sm:px-6 lg:px-8";
const PANEL = "rounded-lg border bg-background";
const PANEL_HEAD = "flex min-h-11 items-center justify-between gap-3 border-b px-4";
const REFRESH_MS = 15_000;

async function detail(response: Response, fallback: string) {
  const data = await response.json().catch(() => null);
  return typeof data?.detail === "string" ? data.detail : fallback;
}

function clock(seconds: number | null) {
  if (seconds == null) return "–";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function ReformularPage() {
  const t = useTranslations("reform");
  const { data: session, isPending } = useSession();
  const access = useLiveAccess(Boolean(session?.user?.id));
  const [url, setUrl] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [page, setPage] = useState(1);
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [kits, setKits] = useState<Kit[]>([]);
  const [kit, setKit] = useState("catclips_v2");
  const [autoPublish, setAutoPublish] = useState(false);
  const [sending, setSending] = useState(false);
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadItems = useCallback(async () => {
    const response = await fetch("/api/live/reform/items", { cache: "no-store" });
    if (response.ok) setItems(((await response.json()) as { items: Item[] }).items);
  }, []);

  useEffect(() => {
    if (!access) return;
    void loadItems();
    const timer = setInterval(() => void loadItems(), REFRESH_MS);
    fetch("/api/live/edit-kits", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => data && setKits((data.kits ?? data) as Kit[]))
      .catch(() => undefined);
    fetch("/api/live/postiz/accounts", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { targets?: Target[] } | null) => {
        const usable = (data?.targets ?? []).filter((target) => target.enabled && !target.problem);
        setTargets(usable);
        setTargetIds(usable.map((target) => target.id));
      })
      .catch(() => undefined);
    return () => clearInterval(timer);
  }, [access, loadItems]);

  const search = async (nextPage: number) => {
    setSearching(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/live/reform/list?url=${encodeURIComponent(url.trim())}&page=${nextPage}`, { cache: "no-store" });
      if (!response.ok) {
        const reason = await detail(response, "");
        throw new Error(reason === "instagram" ? t("errors.instagram") : reason === "unsupported" ? t("errors.unsupported") : t("errors.read"));
      }
      const data = (await response.json()) as Listing;
      setListing((current) => (nextPage > 1 && current ? { ...data, items: [...current.items, ...data.items] } : data));
      if (nextPage === 1) setChosen([]);
      setPage(nextPage);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const toggle = (list: string[], id: string, on: boolean) => (on ? [...list, id] : list.filter((value) => value !== id));

  const send = async () => {
    if (!listing) return;
    setSending(true);
    setError(null);
    try {
      const picked = listing.items.filter((video) => chosen.includes(video.id));
      const response = await fetch("/api/live/reform", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: picked.map((video) => ({ platform: listing.platform, id: video.id, title: video.title, duration: video.duration, channel: listing.channel })),
          edit_kit: kit,
          target_ids: autoPublish ? targetIds : [],
          auto_publish: autoPublish,
        }),
      });
      if (!response.ok) throw new Error(await detail(response, t("errors.send")));
      const result = (await response.json()) as { queued: number; skipped: number };
      setNotice(t("queued", { count: result.queued }));
      setListing((current) => current && { ...current, items: current.items.map((video) => (chosen.includes(video.id) ? { ...video, taken: true } : video)) });
      setChosen([]);
      void loadItems();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
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
    <main className={`${PAGE} space-y-6`}>
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </header>

      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          if (url.trim()) void search(1);
        }}
      >
        <Input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={t("placeholder")} aria-label={t("placeholder")} />
        <Button type="submit" disabled={searching || !url.trim()}>
          {searching && page === 1 ? <Loader2 className="size-4 animate-spin" /> : null}
          {t("search")}
        </Button>
      </form>
      <p className="-mt-4 text-xs text-muted-foreground">{t("sources")}</p>

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {notice ? (
        <Alert>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      {listing ? (
        <section className={PANEL}>
          <div className={PANEL_HEAD}>
            <span className="text-sm font-medium">{t("videosOf", { channel: listing.channel })}</span>
            <span className="text-xs text-muted-foreground">{t("chosen", { count: chosen.length })}</span>
          </div>
          {listing.items.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">{t("empty")}</p>
          ) : (
            <ul className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {listing.items.map((video) => {
                const disabled = video.taken || video.too_long;
                return (
                  <li key={video.id}>
                    <label className={`flex gap-3 rounded-md border p-2 ${disabled ? "opacity-50" : "cursor-pointer hover:bg-muted/50"}`}>
                      <Checkbox
                        className="mt-1"
                        disabled={disabled}
                        checked={chosen.includes(video.id)}
                        onCheckedChange={(value) => setChosen((list) => toggle(list, video.id, value === true))}
                      />
                      {video.thumbnail ? (
                        // eslint-disable-next-line @next/next/no-img-element -- remote platform thumbnails
                        <img src={video.thumbnail} alt="" className="h-20 w-14 shrink-0 rounded object-cover" loading="lazy" />
                      ) : (
                        <div className="h-20 w-14 shrink-0 rounded bg-muted" />
                      )}
                      <div className="min-w-0 space-y-1">
                        <p className="line-clamp-2 text-sm">{video.title || video.id}</p>
                        <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                          <span className="tabular-nums">{clock(video.duration)}</span>
                          {video.short ? <Badge variant="outline">{t("tags.short")}</Badge> : null}
                          {video.too_long ? <Badge variant="outline">{t("tags.tooLong")}</Badge> : null}
                          {video.taken ? <Badge variant="secondary">{t("tags.taken")}</Badge> : null}
                        </div>
                      </div>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          {listing.items.length >= page * 30 ? (
            <div className="border-t p-3 text-center">
              <Button variant="ghost" size="sm" disabled={searching} onClick={() => void search(page + 1)}>
                {searching && page > 0 ? <Loader2 className="size-4 animate-spin" /> : null}
                {t("more")}
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}

      {listing ? (
        <section className={`${PANEL} space-y-4 p-4`}>
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor="reform-kit" className="text-sm">{t("kit")}</Label>
            <select
              id="reform-kit"
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={kit}
              onChange={(event) => setKit(event.target.value)}
            >
              {(kits.length ? kits : [{ id: "catclips_v2", name: "Cat Clips v2" }]).filter((k) => k.id !== "none").map((k) => (
                <option key={k.id} value={k.id}>{k.name}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-3">
            <Switch id="reform-auto" checked={autoPublish} onCheckedChange={setAutoPublish} />
            <Label htmlFor="reform-auto" className="text-sm">{t("autoPublish")}</Label>
          </div>
          {autoPublish ? (
            targets.length ? (
              <div className="flex flex-wrap gap-2">
                {targets.map((target) => (
                  <label key={target.id} className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm">
                    <Checkbox
                      checked={targetIds.includes(target.id)}
                      onCheckedChange={(value) => setTargetIds((list) => toggle(list, target.id, value === true))}
                    />
                    <span className="font-medium">{target.name}</span>
                    <span className="text-xs capitalize text-muted-foreground">{target.provider}</span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t("noAccounts")}</p>
            )
          ) : (
            <p className="text-xs text-muted-foreground">{t("manualHint")}</p>
          )}
          <Button onClick={() => void send()} disabled={sending || chosen.length === 0 || (autoPublish && targetIds.length === 0)}>
            {sending ? <Loader2 className="size-4 animate-spin" /> : null}
            {t("submit", { count: chosen.length })}
          </Button>
        </section>
      ) : null}

      <section className={PANEL}>
        <div className={PANEL_HEAD}>
          <span className="text-sm font-medium">{t("progress")}</span>
        </div>
        {items === null ? (
          <Skeleton className="m-4 h-10" />
        ) : items.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">{t("nothingYet")}</p>
        ) : (
          <ul className="divide-y">
            {items.map((item) => (
              <li key={item.id} className="flex min-h-11 items-center gap-3 px-4 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground" title={item.reason ?? undefined}>
                  {t(`status.${item.status}` as "status.pending")}
                </span>
                {item.task_id ? (
                  <Link href={`/tasks/${item.task_id}`} className="shrink-0 text-xs underline">
                    {t("open")}
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
