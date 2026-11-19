"""Draw the Cat Clips end card (1080x1920 PNG) from the mascot and the brand font.

Run once after changing the mascot or the copy: python make_end_card.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
FONT = HERE.parents[2] / "fonts" / "GochiHand-Regular.ttf"
YELLOW, BLACK, CREAM = "#FEC803", "#111111", "#F3E9D2"
W, H = 1080, 1920


def centered(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill, shadow=None) -> None:
    width = draw.textlength(text, font=font)
    x = (W - width) / 2
    if shadow:
        draw.text((x + 6, y + 6), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def main() -> None:
    card = Image.new("RGBA", (W, H), YELLOW)
    avatar = ImageOps.fit(Image.open(HERE / "avatar.jpg").convert("RGBA"), (760, 760))
    mask = Image.new("L", avatar.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, *avatar.size), fill=255)
    card.paste(avatar, ((W - 760) // 2, 760 - 380), mask)
    draw = ImageDraw.Draw(card)
    centered(draw, 1200, "CAT CLIPS", ImageFont.truetype(str(FONT), 150), BLACK, shadow=CREAM)
    centered(draw, 1400, "Siga pra mais", ImageFont.truetype(str(FONT), 80), BLACK)
    card.save(HERE / "end_card.png")


if __name__ == "__main__":
    main()
