"""Sensitive blur (fork feature): heavy gore found by Claude on contact sheets.

Frames sampled once a second are tiled 4x4 into sheets, each tile stamped with
its second. Claude (Haiku, through the subscription CLI) names the sheet, tile
and 4x4 grid cells that show heavy gore; those cells become blur regions for
that second.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import cv2
import numpy as np

from ..claude_cli import run_cli_images

logger = logging.getLogger(__name__)

TILES = 16  # 4x4 tiles per sheet
TILE_W = 320
GRID = 4  # each tile is split 4x4 for the regions
ATTEMPTS = 3

GORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gore": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sheet": {"type": "integer"},
                    "tile": {"type": "integer"},
                    "cells": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["sheet", "tile", "cells"],
            },
        }
    },
    "required": ["gore"],
}

PROMPT = """These images are contact sheets of frames from a GTA gameplay clip. Each sheet is a 4x4 grid of tiles, numbered 1-16 left to right, top to bottom, and each tile shows its second in the corner. Sheets are numbered in the order given, starting at 1.

Find HEAVY GORE only: pools of blood, open wounds, dismembered bodies, close-up executions. Ignore ordinary blood sprays from gunfire, small splatter, game HUD, minimaps and the streamer's webcam.

For each tile with heavy gore, answer its sheet, tile and the cells of that tile that contain it: split the tile into a 4x4 grid, cells 1-16 left to right, top to bottom. If no tile has heavy gore, answer an empty list."""


def contact_sheets(frames: list[tuple[float, np.ndarray]]) -> list[tuple[bytes, list[float]]]:
    """JPEG sheets of up to 16 tiles, each with the seconds of its tiles in order."""
    sheets = []
    for first in range(0, len(frames), TILES):
        batch = frames[first:first + TILES]
        h, w = batch[0][1].shape[:2]
        tile_h = round(TILE_W * h / w)
        sheet = np.zeros((tile_h * 4, TILE_W * 4, 3), dtype=np.uint8)
        for i, (second, frame) in enumerate(batch):
            tile = cv2.resize(frame, (TILE_W, tile_h))
            cv2.putText(tile, f"{second:.0f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(tile, f"{second:.0f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            row, col = divmod(i, 4)
            sheet[row * tile_h:(row + 1) * tile_h, col * TILE_W:(col + 1) * TILE_W] = tile
        ok, jpeg = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("could not encode a contact sheet")
        sheets.append((jpeg.tobytes(), [second for second, _ in batch]))
    return sheets


def gore_regions(answer: dict[str, Any], sheets_times: list[list[float]]) -> list[dict[str, Any]]:
    """Claude's (sheet, tile, cells) answer as blur regions; anything out of range is ignored."""
    regions = []
    for hit in answer.get("gore") or []:
        try:
            times = sheets_times[int(hit["sheet"]) - 1] if int(hit["sheet"]) >= 1 else None
            second = times[int(hit["tile"]) - 1] if times and 1 <= int(hit["tile"]) <= len(times) else None
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if second is None:
            continue
        for cell in hit.get("cells") or []:
            if not isinstance(cell, int) or not 1 <= cell <= GRID * GRID:
                continue
            row, col = divmod(cell - 1, GRID)
            regions.append({"kind": "gore", "start": second, "end": second + 1.0,
                            "x": col / GRID, "y": row / GRID, "w": 1 / GRID, "h": 1 / GRID})
    return regions


def find_gore(frames: list[tuple[float, np.ndarray]], run: Callable[..., dict] = run_cli_images) -> list[dict[str, Any]]:
    """Heavy gore regions in one-per-second frames; raises if Claude can't answer after retries."""
    if not frames:
        return []
    sheets = contact_sheets(frames)
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            answer = run(PROMPT, [jpeg for jpeg, _ in sheets], GORE_SCHEMA)
            return gore_regions(answer, [times for _, times in sheets])
        except Exception as exc:  # the CLI, the network or a refusal
            last = exc
            logger.warning("gore check attempt %d failed: %s", attempt + 1, exc)
    raise RuntimeError(f"gore check failed after {ATTEMPTS} attempts: {last}")
