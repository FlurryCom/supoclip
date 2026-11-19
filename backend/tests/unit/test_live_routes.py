import pytest
from fastapi import HTTPException

from src.api.routes import live
from src.api.routes.live import _channel, _channel_name, _settings


@pytest.mark.parametrize("raw", ["Gaules", "@gaules", "https://www.twitch.tv/gaules", "twitch.tv/gaules/", "m.twitch.tv/Gaules"])
def test_channel_name_accepts_names_and_links(raw):
    assert _channel_name(raw) == "gaules"


@pytest.mark.parametrize("raw", ["", "ab", "https://youtube.com/gaules", "gau les", "x" * 26, "../etc"])
def test_channel_name_rejects_the_rest(raw):
    with pytest.raises(HTTPException) as error:
        _channel_name(raw)
    assert error.value.status_code == 422


def test_settings_keeps_only_known_valid_keys():
    assert _settings({"enabled": False, "min_score": 70, "chat_weight": 60, "user_id": "someone-else"}) == {"enabled": False, "min_score": 70, "chat_weight": 60}


@pytest.mark.parametrize("data", [{"min_score": 101}, {"min_score": True}, {"chat_weight": -1}, {"chat_weight": 101}, {"daily_window_cap": 0}, {"chat_filter": "yes"}])
def test_settings_rejects_bad_values(data):
    with pytest.raises(HTTPException):
        _settings(data)


def test_target_settings_accept_only_known_values():
    from src.api.routes.live import _target_settings

    assert _target_settings({"mode": "now", "min_score": 85, "integration_id": "x"}) == {"mode": "now", "min_score": 85}
    for bad in ({"mode": "schedule"}, {"min_score": 120}, {"daily_cap": 0}, {"enabled": "yes"}):
        with pytest.raises(HTTPException):
            _target_settings(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    ("https://www.youtube.com/@LOUDCoringa", ("youtube", "@loudcoringa")),
    ("youtube.com/@LOUDCoringa/live", ("youtube", "@loudcoringa")),
    ("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", ("youtube", "channel/UCabcdefghijklmnopqrstuv")),
    ("Gaules", ("twitch", "gaules")),
    ("https://www.twitch.tv/gaules", ("twitch", "gaules")),
])
async def test_channel_detects_the_platform(raw, expected):
    assert await _channel(raw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["https://www.youtube.com/watch?v=QhKWnCPIhiU", "https://youtu.be/QhKWnCPIhiU", "youtube.com/live/QhKWnCPIhiU"])
async def test_video_links_follow_their_channel(raw, monkeypatch):
    seen = []
    monkeypatch.setattr(live, "_youtube_channel_of_video", lambda video_id: seen.append(video_id) or "@loudcoringa")
    assert await _channel(raw) == ("youtube", "@loudcoringa")
    assert seen == ["QhKWnCPIhiU"]
