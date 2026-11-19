from datetime import datetime, timezone

import pytest

from supoclip_live.postiz import build_post, caption, provider_settings, refusal_reason

TARGET = {"integration_id": "abc", "provider": "tiktok", "name": "Dossiê", "mode": "draft"}


def accounts(**changes):
    account = {"id": "abc", "provider": "tiktok", "name": "Dossiê", "disabled": False}
    account.update(changes)
    return [account, {"id": "other", "provider": "youtube", "name": "Arq-X", "disabled": False}]


def test_same_account_is_allowed():
    assert refusal_reason(TARGET, accounts()) is None


@pytest.mark.parametrize("changes,fragment", [
    ({"id": "gone"}, "no longer connected"),
    ({"provider": "youtube"}, "now youtube"),
    ({"name": "Arq-X"}, "now named 'Arq-X'"),
    ({"disabled": True}, "disabled"),
])
def test_any_change_refuses_the_post(changes, fragment):
    assert fragment in refusal_reason(TARGET, accounts(**changes))


def test_post_goes_only_to_the_target_integration():
    body = build_post(TARGET, {"id": "m1", "path": "https://x/m1.mp4"}, "Big moment", "caption",
                      datetime(2026, 11, 19, 12, tzinfo=timezone.utc))
    assert body["type"] == "draft"
    assert [p["integration"]["id"] for p in body["posts"]] == ["abc"]
    assert body["posts"][0]["settings"]["__type"] == "tiktok"
    assert body["posts"][0]["value"][0]["image"] == [{"id": "m1", "path": "https://x/m1.mp4"}]
    assert body["date"] == "2026-11-19T12:00:00.000Z"


def test_provider_settings_respect_title_limits():
    assert len(provider_settings("tiktok", "x" * 200)["title"]) == 90
    assert len(provider_settings("youtube", "x" * 200)["title"]) == 100
    with pytest.raises(ValueError):
        provider_settings("x", "title")


def test_caption_credits_the_streamer():
    text = caption("Wheelie through traffic", "caseoh_", "GTA 6 FIRST TIME")
    assert "twitch.tv/caseoh_" in text and "#gta6" in text and "#caseoh" in text
