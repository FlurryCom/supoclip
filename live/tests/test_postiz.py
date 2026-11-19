from datetime import datetime, timezone

import pytest

from supoclip_live.postiz import build_post, caption, effective_visibility, provider_settings, refusal_reason

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


def test_caption_credits_a_youtube_channel():
    text = caption("Big moment", "@loudcoringa", "GTA VI ESTÁ ENTRE NÓS", "youtube")
    assert "youtube.com/@loudcoringa" in text and "#youtube" in text and "#loudcoringa" in text


def test_private_visibility_reaches_each_platform():
    assert provider_settings("tiktok", "t", "private")["privacy_level"] == "SELF_ONLY"
    assert provider_settings("tiktok", "t")["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert provider_settings("youtube", "t", "private")["type"] == "private"
    assert provider_settings("youtube", "t")["type"] == "public"


def test_post_uses_the_targets_visibility():
    body = build_post({**TARGET, "visibility": "private"}, {"id": "m1", "path": "p"}, "T", "c")
    assert body["posts"][0]["settings"]["privacy_level"] == "SELF_ONLY"


@pytest.mark.parametrize("account,channel,expected", [
    ("public", "default", "public"),
    ("public", "unlisted", "unlisted"),
    ("public", "private", "private"),
    ("private", "default", "private"),
    ("private", "unlisted", "private"),
    ("unlisted", "default", "unlisted"),
])
def test_the_more_restrictive_visibility_wins(account, channel, expected):
    assert effective_visibility(account, channel) == expected


def test_unlisted_means_unlisted_on_youtube_and_only_me_on_tiktok():
    assert provider_settings("youtube", "t", "unlisted")["type"] == "unlisted"
    assert provider_settings("tiktok", "t", "unlisted")["privacy_level"] == "SELF_ONLY"


def test_held_back_instagram_clips_wait_as_drafts():
    target = {**TARGET, "provider": "instagram", "mode": "now"}
    media = {"id": "m1", "path": "p"}
    assert build_post({**target, "visibility": "unlisted"}, media, "T", "c")["type"] == "draft"
    assert build_post({**target, "visibility": "public"}, media, "T", "c")["type"] == "now"
