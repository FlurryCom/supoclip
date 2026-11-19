import asyncio

import pytest

from src import twitch_trending as tt

HELIX = {"data": [
    {"user_login": "lirik", "user_name": "LIRIK", "viewer_count": 16200, "language": "en",
     "game_name": "Grand Theft Auto VI", "title": "GTA VII Playthru"},
    {"user_login": "ohnepixel", "user_name": "ohnePixel", "viewer_count": 57300, "language": "en",
     "game_name": "Grand Theft Auto VI", "title": "GTA 6 FULL PLAYTHROUGH"},
]}


def test_trending_rows_sort_by_viewers_and_mark_monitored():
    rows = tt.trending_rows(HELIX, {"lirik"})
    assert [r["login"] for r in rows] == ["ohnepixel", "lirik"]
    assert rows[0] == {"login": "ohnepixel", "name": "ohnePixel", "viewers": 57300, "language": "en",
                       "game": "Grand Theft Auto VI", "title": "GTA 6 FULL PLAYTHROUGH", "monitored": False}
    assert rows[1]["monitored"] is True


def test_missing_keys_mean_not_configured(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    with pytest.raises(tt.TwitchNotConfigured):
        asyncio.run(tt.top_streams("gta6", "en"))


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, url, params=None, timeout=None):
        self.calls.append(("token", params))
        return FakeResponse({"access_token": "tok", "expires_in": 3600})

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("/games"):
            return FakeResponse({"data": [{"id": "999", "name": "Grand Theft Auto VI"}]})
        return FakeResponse(HELIX)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def test_top_streams_filters_and_reuses_the_token(monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    tt._cache.clear()
    http = FakeHttp()
    asyncio.run(tt.top_streams("gta6", "en", http=http))
    asyncio.run(tt.top_streams("all", "all", http=http))
    kinds = [kind for kind, _ in http.calls]
    assert kinds.count("token") == 1 and kinds.count("games") == 1  # both cached
    streams = [params for kind, params in http.calls if kind == "streams"]
    assert streams[0] == {"first": 20, "game_id": "999", "language": "en"}
    assert streams[1] == {"first": 20}  # "all" filters are left out
