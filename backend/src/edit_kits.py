"""Edit kits (fork feature): a switchable look for live clips.

A kit names a caption template plus the extras burned on the finished clip by
``media/edit_kit_render.apply_edit_kit``: real chat at the chat peak, sound
effects and an optional end card. Each live channel picks one; ``none`` is SupoClip's
normal look. A new kit is one more entry here plus its assets folder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

KITS: dict[str, dict[str, Any]] = {
    "none": {
        "id": "none",
        "name": "SupoClip",
        "caption_template": None,
        "chat_overlay": False,
        "burn_hook_title": True,
        "layout": None,
        "sfx": [],
        "end_card": None,
        "cta_seconds": 0.0,
        "colors": {},
    },
    "catclips": {
        "id": "catclips",
        "name": "Cat Clips",
        "caption_template": "catclips",
        "chat_overlay": True,
        "burn_hook_title": True,
        "layout": None,
        "sfx": [
            {"path": ASSETS_DIR / "sfx" / "bell.wav", "at": "start", "volume": 1.0},
            {"path": ASSETS_DIR / "sfx" / "cartoon-whoosh.wav", "at": "start", "volume": 0.5},
            {"path": ASSETS_DIR / "sfx" / "boom.wav", "at": "peak", "volume": 0.8},
        ],
        "end_card": None,
        # The recorder adds each platform's follow CTA over the last seconds at posting time.
        "cta_seconds": 2.5,
        "colors": {"yellow": "#FEC803", "black": "#111111", "cream": "#F3E9D2"},
    },
}
KITS["catclips_v2"] = {
    **KITS["catclips"],
    "id": "catclips_v2",
    "name": "Cat Clips v2",
    # Owner (2026-11-20): streamer on top, title on a yellow bar, gameplay below with the captions;
    # no chat bubbles or title over the video (the title lives in the bar).
    "chat_overlay": False,
    "burn_hook_title": False,
    "layout": "streamer_split",
}


def get_kit(name: str | None) -> dict[str, Any]:
    """The kit called ``name``; unknown or missing names get the normal look."""
    return KITS.get(name or "none", KITS["none"])


def kit_choices() -> list[dict[str, str]]:
    return [{"id": kit["id"], "name": kit["name"]} for kit in KITS.values()]
