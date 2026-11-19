"""The SupoClip API, used exactly as the web app uses it.

Windows go through POST /upload and POST /tasks/ with the frontend's signed
headers, so preflight, minute charging, admission and dedupe all apply.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import httpx


class Backend:
    def __init__(self, base_url: str, secret: str):
        if not secret:
            raise RuntimeError("BACKEND_AUTH_SECRET is required for the live recorder")
        self.secret = secret
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(60, read=900, write=900))

    def _headers(self, user_id: str, idempotency_key: str | None = None) -> dict[str, str]:
        ts = str(int(time.time()))
        sig = hmac.new(self.secret.encode(), f"{user_id}:{ts}".encode(), hashlib.sha256).hexdigest()
        headers = {"x-supoclip-user-id": user_id, "x-supoclip-ts": ts, "x-supoclip-signature": sig}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def upload(self, user_id: str, path: Path, key: str) -> str:
        with path.open("rb") as video:
            response = await self.http.post(
                "/upload",
                headers=self._headers(user_id, key),
                files={"video": (path.name, video, "video/mp4")},
            )
        response.raise_for_status()
        return response.json()["video_path"]

    async def create_task(
        self, user_id: str, video_path: str, title: str, key: str, clip_length: str, instructions: str | None = None,
    ) -> str:
        body = {"source": {"url": video_path, "title": title}, "clip_length": clip_length}
        if instructions:  # creator instructions reach the clip selection prompt
            body["instructions"] = instructions
        response = await self.http.post("/tasks/", headers=self._headers(user_id, key), json=body)
        response.raise_for_status()
        return response.json()["task_id"]

    async def delete_clip(self, user_id: str, task_id: str, clip_id: str) -> None:
        response = await self.http.delete(f"/tasks/{task_id}/clips/{clip_id}", headers=self._headers(user_id))
        if response.status_code != 404:
            response.raise_for_status()

    async def download_clip(self, user_id: str, task_id: str, clip_id: str, dest: Path) -> None:
        async with self.http.stream("GET", f"/tasks/{task_id}/clips/{clip_id}/file", headers=self._headers(user_id)) as response:
            response.raise_for_status()
            with dest.open("wb") as out:
                async for block in response.aiter_bytes(1 << 20):
                    out.write(block)

    async def release_source(self, user_id: str, window_id: str) -> None:
        """Delete a finished window's uploaded copy on the backend (its clips stay)."""
        response = await self.http.post(f"/live/windows/{window_id}/release-source", headers=self._headers(user_id))
        response.raise_for_status()

    async def close(self) -> None:
        await self.http.aclose()
