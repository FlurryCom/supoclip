"""Draw the per-platform follow/subscribe CTAs for an edit kit (2.5 s, alpha, with sound).

A mouse pointer slides into a dark card, taps the platform's button and the
button flips to its followed state. English button labels only. Output:
live/assets/cta/<kit>/<provider>.mov (PNG codec with alpha + PCM audio), which
the recorder lays over the last seconds of each clip before posting it.

Run once after changing the look: python live/tools/make_cta.py
"""
from __future__ import annotations

import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ASSETS = ROOT / "backend" / "assets"
FONT = ROOT / "backend" / "fonts" / "ArchivoBlack-Regular.ttf"
OUT = ROOT / "live" / "assets" / "cta"

W, H, FPS, SECONDS = 1080, 1920, 30, 2.5
SS = 2  # draw at 2x and downscale, for smooth edges
RATE = 48000
CARD = (17, 17, 17, 217)
TIKTOK_RED = (254, 44, 85, 255)
DONE_GREY = (63, 63, 63, 255)
WHITE, BLACK = (255, 255, 255, 255), (17, 17, 17, 255)

# Owner: the CTA took too much screen. It is drawn at full size, then shrunk and
# moved to the bottom centre, just under the burned-in captions.
SCALE = 0.65
FINAL_CY = H * 0.85

# Timeline (seconds).
CARD_IN, POINTER_FROM, POINTER_TO, PRESS, TOGGLE, FADE_FROM = 0.30, 0.30, 1.05, 1.05, 1.10, 2.20


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def avatar(size: int) -> Image.Image:
    face = ImageOps.fit(Image.open(BACKEND_ASSETS / "brand" / "catclips" / "avatar.jpg").convert("RGBA"), (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    face.putalpha(mask)
    return face


def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), px)


def pill(draw, box, fill=None, outline=None, width=0):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=(y1 - y0) // 2, fill=fill, outline=outline, width=width)


def centered_text(draw, center, text, px, fill):
    f = font(px)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=f)
    draw.text((center[0] - (right - left) / 2 - left, center[1] - (bottom - top) / 2 - top), text, font=f, fill=fill)


def bell_icon(size: int, angle: float) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    s = size
    d.pieslice((s * 0.18, s * 0.12, s * 0.82, s * 0.85), 180, 360, fill=WHITE)
    d.rectangle((s * 0.18, s * 0.48, s * 0.82, s * 0.70), fill=WHITE)
    d.rounded_rectangle((s * 0.08, s * 0.66, s * 0.92, s * 0.76), radius=int(s * 0.05), fill=WHITE)
    d.ellipse((s * 0.42, s * 0.76, s * 0.58, s * 0.92), fill=WHITE)
    d.ellipse((s * 0.45, s * 0.04, s * 0.55, s * 0.14), fill=WHITE)
    return icon.rotate(angle, resample=Image.BICUBIC, center=(s / 2, s * 0.1))


def pointer(draw, tip, scale):
    shape = [(0, 0), (0, 100), (24, 77), (42, 116), (58, 109), (40, 71), (72, 71)]
    k = 1.1 * scale * SS
    pts = [(tip[0] + x * k, tip[1] + y * k) for x, y in shape]
    draw.polygon(pts, fill=WHITE, outline=BLACK, width=int(6 * SS))


def layout(provider: str) -> dict:
    """Card box and the button's target point, at 1x."""
    cx, cy = W / 2, H * 0.58  # clear of the burned-in captions at 0.74 of the height
    if provider == "tiktok":
        cy = H * 0.55
        return {"card": (cx - 210, cy - 210, cx + 210, cy + 210), "avatar": (cx, cy - 30, 110), "target": (cx, cy + 80),
                "tip": (cx + 22, cy + 100)}
    return {"card": (cx - 380, cy - 110, cx + 380, cy + 110), "avatar": (cx - 270, cy, 60),
            "pill": (cx - 175, cy - 55, cx + 345, cy + 55), "target": (cx + 85, cy),
            "tip": (cx + 285, cy + 20)}  # the pointer rests on the pill's end, clear of the label


def frame(provider: str, t: float, faces: dict[int, Image.Image]) -> Image.Image:
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    L = layout(provider)
    grow = 0.8 + 0.2 * ease_out(t / CARD_IN)
    cx, cy = (L["card"][0] + L["card"][2]) / 2, (L["card"][1] + L["card"][3]) / 2

    def at(x, y):  # 1x layout point -> scaled 2x canvas point around the card centre
        return ((cx + (x - cx) * grow) * SS, (cy + (y - cy) * grow) * SS)

    x0, y0 = at(L["card"][0], L["card"][1])
    x1, y1 = at(L["card"][2], L["card"][3])
    d.rounded_rectangle((x0, y0, x1, y1), radius=int(48 * SS * grow), fill=CARD)
    done = t >= TOGGLE

    ax, ay, ar = L["avatar"]
    size = max(2, int(2 * ar * grow * SS))
    face = faces.get(size) or faces.setdefault(size, avatar(size))
    px, py = at(ax, ay)
    img.alpha_composite(face, (int(px - size / 2), int(py - size / 2)))

    if provider == "tiktok":
        bx, by = at(*L["target"])
        r = 44 * grow * SS
        d.ellipse((bx - r, by - r, bx + r, by + r), fill=DONE_GREY if done else TIKTOK_RED, outline=WHITE, width=int(4 * SS))
        w = int(7 * SS)
        if done:
            d.line([(bx - r * 0.45, by + r * 0.02), (bx - r * 0.1, by + r * 0.38), (bx + r * 0.5, by - r * 0.35)], fill=WHITE, width=w, joint="curve")
        else:
            d.line([(bx - r * 0.5, by), (bx + r * 0.5, by)], fill=WHITE, width=w)
            d.line([(bx, by - r * 0.5), (bx, by + r * 0.5)], fill=WHITE, width=w)
    else:
        box = (*at(L["pill"][0], L["pill"][1]), *at(L["pill"][2], L["pill"][3]))
        px_text = int(46 * grow * SS)
        label_center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if provider == "youtube":
            if done:
                pill(d, box, fill=DONE_GREY)
                bell_size = int(60 * grow * SS)
                wiggle = 15 * math.sin((t - TOGGLE) * 2 * math.pi * 5) * max(0.0, 1 - (t - TOGGLE) / 0.6)
                img.alpha_composite(bell_icon(bell_size, wiggle), (int(box[0] + 28 * SS), int(label_center[1] - bell_size / 2)))
                centered_text(d, (label_center[0] + 34 * SS, label_center[1]), "Subscribed", px_text, WHITE)
            else:
                pill(d, box, fill=WHITE)
                centered_text(d, label_center, "Subscribe", px_text, BLACK)
        else:
            if done:
                pill(d, box, fill=DONE_GREY)
                centered_text(d, label_center, "Following", px_text, WHITE)
            else:
                pill(d, box, outline=WHITE, width=int(4 * SS))
                centered_text(d, label_center, "Follow", px_text, WHITE)

    # The pointer slides in, presses, and stays on the button.
    if t >= POINTER_FROM:
        tx, ty = L["tip"]
        k = ease_in_out((t - POINTER_FROM) / (POINTER_TO - POINTER_FROM))
        sx, sy = W * 1.6, H * 1.4  # far enough out that it still enters from off-screen once shrunk
        tip = ((sx + (tx - sx) * k) * SS, (sy + (ty - sy) * k) * SS)
        press = 0.85 if PRESS <= t < PRESS + 0.12 else 1.0
        if PRESS <= t < PRESS + 0.35:
            p = (t - PRESS) / 0.35
            rr = (40 + 120 * ease_out(p)) * SS
            ring = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((tx * SS - rr, ty * SS - rr, tx * SS + rr, ty * SS + rr), outline=(255, 255, 255, int(255 * (1 - p))), width=int(6 * SS))
            img.alpha_composite(ring)
        pointer(d, tip, press)

    img = place(img, cy)
    alpha = min(1.0, t / CARD_IN) * (1 - ease_out((t - FADE_FROM) / (SECONDS - FADE_FROM)) if t > FADE_FROM else 1.0)
    if alpha < 1:
        a = img.getchannel("A").point(lambda v: int(v * alpha))
        img.putalpha(a)
    return img.resize((W, H), Image.LANCZOS)


def place(img: Image.Image, cy: float) -> Image.Image:
    """Shrink the drawn CTA around its card centre and move that centre to the bottom of the frame."""
    small = img.resize((round(img.width * SCALE), round(img.height * SCALE)), Image.LANCZOS)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    x = round(W / 2 * SS - W / 2 * SS * SCALE)
    y = round(FINAL_CY * SS - cy * SS * SCALE)
    out.alpha_composite(small, (x, y)) if y >= 0 else out.paste(small.crop((0, -y, small.width, small.height)), (x, 0))
    return out


def read_wav(name: str) -> np.ndarray:
    with wave.open(str(BACKEND_ASSETS / "sfx" / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def soundtrack(provider: str) -> np.ndarray:
    track = np.zeros(int(RATE * SECONDS), dtype=np.float32)
    cues = [("click.wav", PRESS, 0.8), ("pop.wav", TOGGLE, 0.6)]
    if provider == "youtube":
        cues.append(("bell.wav", TOGGLE + 0.2, 1.0))
    for name, at, volume in cues:
        sound = read_wav(name) * volume
        start = int(at * RATE)
        end = min(len(track), start + len(sound))
        track[start:end] += sound[: end - start]
    return np.clip(track, -1, 1)


def build(kit: str, provider: str) -> Path:
    out = OUT / kit / f"{provider}.mov"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        faces: dict[int, Image.Image] = {}
        for i in range(int(FPS * SECONDS)):
            frame(provider, i / FPS, faces).save(tmp / f"{i:03d}.png")
        with wave.open(str(tmp / "audio.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((soundtrack(provider) * 32767).astype(np.int16).tobytes())
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "%03d.png"),
            "-i", str(tmp / "audio.wav"), "-c:v", "png", "-pix_fmt", "rgba", "-c:a", "pcm_s16le",
            "-t", str(SECONDS), str(out),
        ], check=True)
    return out


if __name__ == "__main__":
    for provider in ("tiktok", "youtube", "instagram"):
        print(build("catclips", provider))
