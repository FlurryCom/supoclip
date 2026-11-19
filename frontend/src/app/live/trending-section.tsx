"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface TrendingChannel {
  login: string;
  name: string;
  viewers: number;
  monitored: boolean;
}

interface TrendingState {
  configured: boolean;
  channels: TrendingChannel[];
  at_limit: boolean;
  limit?: number;
}

const REFRESH_MS = 60_000;

function compact(viewers: number, locale: string) {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(viewers);
}

// Most watched Twitch lives right now; one click adds a channel to monitoring.
export function TrendingSection({ onAdded }: { onAdded: () => void }) {
  const t = useTranslations("live.trending");
  const locale = t("locale");
  const [game, setGame] = useState("gta6");
  const [language, setLanguage] = useState("en");
  const [state, setState] = useState<TrendingState | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(`/api/live/trending?game=${game}&language=${language}`, { cache: "no-store" });
    if (!response.ok) throw new Error(t("loadError"));
    setState((await response.json()) as TrendingState);
    setError(null);
  }, [game, language, t]);

  useEffect(() => {
    const refresh = () => load().catch((err: Error) => setError(err.message));
    void refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const add = async (login: string) => {
    setAdding(login);
    const response = await fetch("/api/live/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: login, like_last: true }),
    });
    setAdding(null);
    if (!response.ok) {
      setError(t("addError"));
      return;
    }
    onAdded();
    await load().catch(() => undefined);
  };

  return (
    <section className="mb-6 rounded-xl border bg-background p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <div className="flex gap-2">
          <Select value={game} onValueChange={setGame}>
            <SelectTrigger aria-label={t("game")} className="h-8 w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="gta6">GTA VI</SelectItem>
              <SelectItem value="all">{t("allGames")}</SelectItem>
            </SelectContent>
          </Select>
          <Select value={language} onValueChange={setLanguage}>
            <SelectTrigger aria-label={t("language")} className="h-8 w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="en">{t("english")}</SelectItem>
              <SelectItem value="pt">{t("portuguese")}</SelectItem>
              <SelectItem value="es">{t("spanish")}</SelectItem>
              <SelectItem value="all">{t("allLanguages")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      {error ? <p className="mb-2 text-sm text-destructive">{error}</p> : null}
      {state && !state.configured ? (
        <p className="text-sm text-muted-foreground">{t("notConfigured")}</p>
      ) : state ? (
        <ul className="divide-y">
          {state.channels.map((channel) => (
            <li key={channel.login} className="flex items-center gap-3 py-2">
              <a href={`https://twitch.tv/${channel.login}`} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate font-medium hover:underline">
                {channel.name}
              </a>
              <span className="flex items-center gap-1 text-xs font-semibold text-red-600">
                <span className="size-1.5 rounded-full bg-red-600" aria-hidden /> {t("live")}
              </span>
              <span className="w-16 text-right text-sm tabular-nums text-muted-foreground">{compact(channel.viewers, locale)}</span>
              {channel.monitored ? (
                <Button size="sm" variant="secondary" disabled className="w-28">{t("monitoring")}</Button>
              ) : (
                <Button
                  size="sm"
                  className="w-28"
                  disabled={state.at_limit || adding !== null}
                  title={state.at_limit ? t("atLimit", { limit: state.limit ?? 20 }) : undefined}
                  onClick={() => void add(channel.login)}
                >
                  {adding === channel.login ? t("adding") : t("add")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{t("loading")}</p>
      )}
      {state?.at_limit ? <p className="mt-2 text-xs text-muted-foreground">{t("atLimit", { limit: state.limit ?? 20 })}</p> : null}
    </section>
  );
}
