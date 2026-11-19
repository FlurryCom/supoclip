import pytest
from fastapi import HTTPException

from src.api.routes.live import _channel_name, _settings


@pytest.mark.parametrize("raw", ["Gaules", "@gaules", "https://www.twitch.tv/gaules", "twitch.tv/gaules/", "m.twitch.tv/Gaules"])
def test_channel_name_accepts_names_and_links(raw):
    assert _channel_name(raw) == "gaules"


@pytest.mark.parametrize("raw", ["", "ab", "https://youtube.com/gaules", "gau les", "x" * 26, "../etc"])
def test_channel_name_rejects_the_rest(raw):
    with pytest.raises(HTTPException) as error:
        _channel_name(raw)
    assert error.value.status_code == 422


def test_settings_keeps_only_known_valid_keys():
    assert _settings({"enabled": False, "min_score": 70, "user_id": "someone-else"}) == {"enabled": False, "min_score": 70}


@pytest.mark.parametrize("data", [{"min_score": 101}, {"min_score": True}, {"daily_window_cap": 0}, {"chat_filter": "yes"}])
def test_settings_rejects_bad_values(data):
    with pytest.raises(HTTPException):
        _settings(data)
