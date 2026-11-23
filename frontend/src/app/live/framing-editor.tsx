"use client";

import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, Crop } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

import {
  CORNER_PRESETS, FULL_FRAME, drawBox, fitInside, moveBox, resizeBox, rounded, type Box, type Corner,
} from "./framing-math";

// What the editor needs from a live channel; it saves through the page's own settings flow.
export type FramingChannel = {
  id: string;
  channel: string;
  cam_box: Box | null;
  game_box: Box | null;
  // set when the automatic check (Claude) changed the boxes, or found them wrong without a fix
  framing_changed_at?: string | null;
  framing_notice?: "changed" | "attention" | null;
  framing_reason?: string | null;
  framing_auto?: boolean;
  framing_previous?: { cam_box: Box | null; game_box: Box | null } | null;
};
type Framing = { cam_box?: Box | null; game_box?: Box | null; framing_auto?: boolean };

type Target = "cam" | "game";
type Drag = { kind: "move" | "resize" | "draw"; target: Target; corner?: Corner; x: number; y: number; start: Box };

const COLORS: Record<Target, string> = { cam: "border-red-500 bg-red-500/10", game: "border-sky-500 bg-sky-500/10" };
const CORNERS: Corner[] = ["nw", "ne", "sw", "se"];
// Cat Clips v2 output, 1080x1920: webcam 720 on top, the yellow title bar 200, the game 1000.
const TOP = 720 / 1920, BAR = 200 / 1920, GAME = 1000 / 1920;

// The background that shows exactly `box` of the frame, stretched over its element.
function cropStyle(image: string, box: Box): CSSProperties {
  return {
    backgroundImage: `url(${image})`,
    backgroundSize: `${100 / box.w}% ${100 / box.h}%`,
    backgroundPosition: `${box.w < 1 ? (box.x / (1 - box.w)) * 100 : 0}% ${box.h < 1 ? (box.y / (1 - box.h)) * 100 : 0}%`,
  };
}

// One preview area like the render: the region whole and centred over a blurred copy of itself.
function PreviewArea({ image, box, aspect, share, area, blurOnly = false }: {
  image: string; box: Box; aspect: number; share: number; area: number; blurOnly?: boolean;
}) {
  const fit = fitInside((box.w * aspect) / box.h, area);
  return (
    <div className="relative overflow-hidden" style={{ height: `${share * 100}%` }}>
      <div className="absolute inset-0 scale-110 blur-sm" style={cropStyle(image, box)} />
      {blurOnly ? null : <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ width: `${fit.width}%`, height: `${fit.height}%`, ...cropStyle(image, box) }} />}
    </div>
  );
}

function Editor({ channel, onDone }: { channel: FramingChannel; onDone: (settings: Framing | null) => void }) {
  const t = useTranslations("live.framing");
  const [frame, setFrame] = useState<{ image: string; at: string } | null | "missing">(null);
  const [aspect, setAspect] = useState(16 / 9);
  const [cam, setCam] = useState<Box>(channel.cam_box ?? CORNER_PRESETS.ne);
  const [game, setGame] = useState<Box>(channel.game_box ?? FULL_FRAME);
  const [noCam, setNoCam] = useState(channel.game_box !== null && channel.cam_box === null);
  const [active, setActive] = useState<Target>("cam");
  const drag = useRef<Drag | null>(null);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    fetch(`/api/live/channels/${channel.id}/framing-frame`, { cache: "no-store" })
      .then(async (response) => (response.ok ? response.json() : "missing"))
      .catch(() => "missing" as const)
      .then((data) => { if (live) setFrame(data); });
    return () => { live = false; };
  }, [channel.id]);

  const boxes: Record<Target, [Box, (box: Box) => void]> = { cam: [cam, setCam], game: [game, setGame] };
  const point = (event: PointerEvent) => {
    const rect = stage.current!.getBoundingClientRect();
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
  };
  const begin = (event: PointerEvent, kind: Drag["kind"], target: Target, corner?: Corner) => {
    event.stopPropagation();
    event.preventDefault();
    stage.current!.setPointerCapture(event.pointerId);
    const [x, y] = point(event);
    drag.current = { kind, target, corner, x, y, start: boxes[target][0] };
    setActive(target);
  };
  const onMove = (event: PointerEvent) => {
    const current = drag.current;
    if (!current) return;
    const [x, y] = point(event);
    const set = boxes[current.target][1];
    if (current.kind === "move") set(moveBox(current.start, x - current.x, y - current.y));
    else if (current.kind === "resize") set(resizeBox(current.start, current.corner!, x - current.x, y - current.y));
    else set(drawBox(current.x, current.y, x, y));
  };
  const onKey = (event: KeyboardEvent, target: Target) => {
    const step = { ArrowLeft: [-0.01, 0], ArrowRight: [0.01, 0], ArrowUp: [0, -0.01], ArrowDown: [0, 0.01] }[event.key];
    if (!step) return;
    event.preventDefault();
    const [box, set] = boxes[target];
    // shift: resize from the bottom-right corner, so the keyboard can do what the corner handles do
    set(event.shiftKey ? resizeBox(box, "se", step[0], step[1]) : moveBox(box, step[0], step[1]));
  };

  const shown: Target[] = noCam ? ["game"] : ["game", "cam"];
  const image = typeof frame === "object" && frame ? frame.image : null;
  const apply = () => onDone({ cam_box: noCam ? null : rounded(cam), game_box: rounded(game) });

  return (
    <>
      <div className="grid gap-5 md:grid-cols-[1fr_9rem]">
        <div className="min-w-0 space-y-3">
          {frame === null ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
          {frame === "missing" ? <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">{t("noFrame")}</p> : null}
          {image ? (
            <>
              <div
                ref={stage}
                className="relative cursor-crosshair touch-none select-none overflow-hidden rounded-md bg-black"
                onPointerDown={(event) => begin(event, "draw", noCam ? "game" : active)}
                onPointerMove={onMove}
                onPointerUp={() => { drag.current = null; }}
                onPointerCancel={() => { drag.current = null; }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- a data URL from our API */}
                <img
                  src={image}
                  alt={t("frameAlt", { channel: channel.channel })}
                  className="block w-full"
                  draggable={false}
                  onLoad={(event) => setAspect(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight)}
                />
                {shown.map((target) => {
                  const box = boxes[target][0];
                  return (
                    <div
                      key={target}
                      role="group"
                      tabIndex={0}
                      aria-label={t("boxLabel", { name: t(target) })}
                      className={`absolute cursor-move border-2 outline-none focus-visible:ring-2 focus-visible:ring-white ${COLORS[target]} ${target === active ? "" : "pointer-events-none"}`}
                      style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
                      onPointerDown={(event) => begin(event, "move", target)}
                      onKeyDown={(event) => onKey(event, target)}
                    >
                      <span className={`absolute left-0 top-0 px-1 text-[11px] font-semibold text-white ${target === "cam" ? "bg-red-500" : "bg-sky-500"}`}>{t(target)}</span>
                      {CORNERS.map((corner) => (
                        <span
                          key={corner}
                          aria-hidden
                          className={`pointer-events-auto absolute size-3 border-2 border-white ${target === "cam" ? "bg-red-500" : "bg-sky-500"} ${corner[0] === "n" ? "-top-1.5" : "-bottom-1.5"} ${corner[1] === "w" ? "-left-1.5" : "-right-1.5"} ${corner === "ne" || corner === "sw" ? "cursor-nesw-resize" : "cursor-nwse-resize"}`}
                          onPointerDown={(event) => begin(event, "resize", target, corner)}
                        />
                      ))}
                    </div>
                  );
                })}
              </div>
              {typeof frame === "object" && frame ? (
                <p className="text-xs text-muted-foreground">{t("frameAt", { time: new Date(frame.at).toLocaleString() })}</p>
              ) : null}
            </>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-2">
              <Switch id={`nocam-${channel.id}`} checked={noCam} onCheckedChange={setNoCam} />
              <Label htmlFor={`nocam-${channel.id}`}>{t("withoutCam")}</Label>
            </div>
            {!noCam ? (
              <>
                <span className="ml-2 text-sm text-muted-foreground">{t("drawing")}</span>
                {(["cam", "game"] as Target[]).map((target) => (
                  <Button key={target} type="button" size="sm" variant={active === target ? "default" : "outline"} aria-pressed={active === target} onClick={() => setActive(target)}>
                    {t(target)}
                  </Button>
                ))}
              </>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!noCam ? (
              <>
                <span className="text-sm text-muted-foreground">{t("corners")}</span>
                {CORNERS.map((corner) => (
                  <Button key={corner} type="button" size="sm" variant="outline" onClick={() => { setCam(CORNER_PRESETS[corner]); setActive("cam"); }}>
                    {t(`corner.${corner}`)}
                  </Button>
                ))}
              </>
            ) : null}
            <Button type="button" size="sm" variant="outline" onClick={() => setGame(FULL_FRAME)}>{t("fullGame")}</Button>
          </div>
        </div>

        {image ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t("preview")}</p>
            <div className="flex aspect-[9/16] w-36 flex-col overflow-hidden rounded-md border bg-black" aria-hidden>
              <PreviewArea image={image} box={noCam ? game : cam} aspect={aspect} share={TOP} area={1080 / 720} blurOnly={noCam} />
              <div className="bg-[#FEC803]" style={{ height: `${BAR * 100}%` }} />
              <PreviewArea image={image} box={game} aspect={aspect} share={GAME} area={1080 / 1000} />
            </div>
          </div>
        ) : null}
      </div>

      <DialogFooter className="flex-wrap gap-2 sm:justify-between">
        <Button type="button" variant="ghost" onClick={() => onDone({ cam_box: null, game_box: null })}>{t("automatic")}</Button>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={() => onDone(null)}>{t("cancel")}</Button>
          <Button type="button" onClick={apply} disabled={!image}>{t("apply")}</Button>
        </div>
      </DialogFooter>
    </>
  );
}

// Per-channel framing for Cat Clips v2: the owner marks the webcam and the game once, every clip follows.
export function FramingSetting({ channel, onChange }: { channel: FramingChannel; onChange: (settings: Framing) => void }) {
  const t = useTranslations("live.framing");
  const [open, setOpen] = useState(false);
  const status = channel.game_box === null ? t("auto") : channel.cam_box === null ? t("noCam") : t("marked");
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>{t("title")}</Label>
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Crop className="size-4" /> {t("open")}
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{status}</p>
      <div className="mt-3 flex items-center gap-2">
        <Switch id={`framing-auto-${channel.id}`} checked={channel.framing_auto ?? true} onCheckedChange={(framing_auto) => onChange({ framing_auto })} />
        <Label htmlFor={`framing-auto-${channel.id}`}>{t("autoSwitch")}</Label>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t(channel.framing_auto ?? true ? "autoOn" : "autoOff")}</p>
      {channel.framing_changed_at ? (
        <Alert className="mt-2 border-amber-500/50 text-amber-700 dark:text-amber-300">
          <AlertTriangle className="size-4" aria-hidden />
          <AlertDescription className="space-y-2">
            <p>
              {t(channel.framing_notice === "changed" ? "notice.changed" : "notice.attention", {
                time: new Date(channel.framing_changed_at).toLocaleString(),
              })}{" "}
              {channel.framing_reason}
            </p>
            <div className="flex flex-wrap gap-2">
              {channel.game_box ? (
                <Button type="button" size="sm" variant="outline" onClick={() => onChange({ cam_box: channel.cam_box, game_box: channel.game_box })}>
                  {t("notice.keep")}
                </Button>
              ) : null}
              <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>{t("notice.review")}</Button>
              {channel.framing_previous ? (
                <Button type="button" size="sm" variant="outline" onClick={() => onChange({ ...channel.framing_previous!, framing_auto: false })}>{t("notice.undo")}</Button>
              ) : null}
            </div>
          </AlertDescription>
        </Alert>
      ) : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{t("dialogTitle", { channel: channel.channel })}</DialogTitle>
            <DialogDescription>{t("hint")}</DialogDescription>
          </DialogHeader>
          {open ? (
            <Editor
              channel={channel}
              onDone={(settings) => {
                setOpen(false);
                if (settings) onChange(settings);
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
