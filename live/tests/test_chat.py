from datetime import datetime, timedelta, timezone

from supoclip_live.chat import SpikeDetector, parse_privmsg

T0 = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)


def feed(detector, bucket_index, users, prefix="u", message="hi"):
    at = T0 + timedelta(seconds=10 * bucket_index)
    spikes = []
    for i in range(users):
        spike = detector.add(f"{prefix}{i}", message, at + timedelta(milliseconds=i))
        if spike:
            spikes.append(spike)
    return spikes


def warm(detector, buckets=20, users=2):
    spikes = []
    for b in range(buckets):
        spikes += feed(detector, b, users)
    return spikes


def test_burst_of_distinct_chatters_is_a_spike():
    d = SpikeDetector()
    assert warm(d) == []
    feed(d, 20, 12)
    assert d.tick(T0 + timedelta(seconds=215)) == T0 + timedelta(seconds=200)


def test_one_spammer_is_not_a_spike():
    d = SpikeDetector()
    warm(d)
    at = T0 + timedelta(seconds=200)
    for i in range(50):
        d.add("spammer", "LOL", at + timedelta(milliseconds=i))
    assert d.tick(T0 + timedelta(seconds=215)) is None


def test_bots_and_commands_do_not_count():
    d = SpikeDetector()
    warm(d)
    feed(d, 20, 12, message="!points")
    assert d.tick(T0 + timedelta(seconds=215)) is None


def test_no_spike_during_warmup():
    d = SpikeDetector()
    feed(d, 0, 1)
    feed(d, 1, 30)
    assert d.tick(T0 + timedelta(seconds=25)) is None


def test_cooldown_reports_one_spike_per_minute():
    d = SpikeDetector()
    warm(d)
    feed(d, 20, 12)
    # The first line of bucket 21 closes bucket 20 and reports it.
    assert feed(d, 21, 12, prefix="v") == [T0 + timedelta(seconds=200)]
    assert d.tick(T0 + timedelta(seconds=222)) is None  # bucket 21 is as hot, but cooling down


def test_parse_privmsg():
    assert parse_privmsg(":ana!ana@ana.tmi.twitch.tv PRIVMSG #chan :que jogada :)") == ("ana", "que jogada :)")
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None


def youtube_line(*messages):
    import json
    actions = [{"clickTrackingParams": "x", "addBannerToLiveChatCommand": {"bannerRenderer": {}}}]
    for author, text, usec in messages:
        actions.append({"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "authorExternalChannelId": author, "message": {"runs": [{"text": text}]}, "timestampUsec": str(usec)}}}})
    actions.append({"addChatItemAction": {"item": {"liveChatViewerEngagementMessageRenderer": {"message": {}}}}})
    return json.dumps({"replayChatItemAction": {"actions": actions}, "isLive": True})


def test_youtube_chat_line_keeps_only_real_messages():
    from supoclip_live.chat import parse_youtube_chat_line
    line = youtube_line(("UCa", "KKKKKK", 1795050000000000), ("UCb", "que isso", 1795050001000000))
    parsed = parse_youtube_chat_line(line)
    assert [(a, t) for a, t, _ in parsed] == [("UCa", "KKKKKK"), ("UCb", "que isso")]
    assert parsed[0][2] == datetime.fromtimestamp(1795050000, tz=timezone.utc)


def test_youtube_chat_line_survives_garbage():
    from supoclip_live.chat import parse_youtube_chat_line
    assert parse_youtube_chat_line("not json") == []
    assert parse_youtube_chat_line('{"replayChatItemAction": {"actions": [{}]}}') == []


def test_keep_message_filters():
    from supoclip_live.chat import BLOCKLIST, keep_message
    assert keep_message("KEKW que isso")
    for bad in ("http://x.com", "olha www.site.com", "@fulano olha", "!points", "a" * 61, "", "   "):
        assert not keep_message(bad), bad
    assert not keep_message("que KYS hein")
    assert keep_message("kysa")  # short stems match whole words (and plurals) only


def test_bucket_keeps_a_sample_of_messages():
    d = SpikeDetector()
    warm(d)
    d.drain()
    feed(d, 20, 25, message="LOL")
    d.tick(T0 + timedelta(seconds=215))
    start, count, spike, messages = d.drain()[-1]
    assert count == 25
    assert len(messages) == 20 and all(m == "LOL" for m in messages)


def test_spammer_lines_count_once_in_sample():
    d = SpikeDetector()
    warm(d)
    d.drain()
    at = T0 + timedelta(seconds=200)
    for i in range(10):
        d.add("spammer", f"spam {i}", at + timedelta(milliseconds=i))
    d.tick(T0 + timedelta(seconds=215))
    assert d.drain()[-1][3] == ["spam 0"]


def test_keep_message_rejects_control_and_surrogate_characters():
    from supoclip_live.chat import keep_message
    assert not keep_message("oi\x00tudo")
    assert not keep_message("oi\ud800")
    assert not keep_message("linha\nquebrada")


def test_keep_message_sees_through_disguises():
    from supoclip_live.chat import BLOCKLIST, keep_message
    slur = next(iter(sorted(BLOCKLIST)))
    assert not keep_message(slur[0] + "​" + slur[1:])  # zero-width space inside the word
    assert not keep_message("".join(chr(ord(c) + 0xFEE0) for c in slur))  # fullwidth letters
    assert not keep_message("entra em meusite.xyz")
    assert not keep_message("meusite dot xyz")
    assert not keep_message("meusite . shop")
    assert keep_message("gg wp")
    assert keep_message("kkkk 10.5 de dano")


def test_a_failing_bucket_store_does_not_stop_the_chat_reader():
    import asyncio
    from supoclip_live.service import Session

    async def broken_store(*_):
        raise RuntimeError("db down")

    stored = Session.safe_store(broken_store, "rec", "chan")
    asyncio.run(stored(T0, 3, False, ["oi"]))  # must not raise


def test_sample_stores_the_cleaned_text():
    d = SpikeDetector()
    warm(d)
    d.drain()
    at = T0 + timedelta(seconds=200)
    d.add("ana", "  ＫＫＫ‮ oi​  ", at)
    d.tick(T0 + timedelta(seconds=215))
    assert d.drain()[-1][3] == ["KKK oi"]


def test_slur_variants_are_caught():
    from supoclip_live.chat import keep_message
    for bad in ("niggers", "viados", "nígger", "nіgger", "n1gger", "niggger", "n i g g e r", "VIADOOO", "f4ggot", "retardados"):
        assert not keep_message(f"olha {bad} kk"), bad


def test_innocent_words_near_short_stems_pass():
    from supoclip_live.chat import keep_message
    for ok in ("spicy food", "que fagulha", "bichano fofo", "kkkkk", "gg ez", "LUL LUL"):
        assert keep_message(ok), ok


def test_phone_numbers_and_non_latin_text_are_refused():
    from supoclip_live.chat import keep_message
    assert not keep_message("liga 11 98765-4321")
    assert not keep_message("привет ребята")
    assert keep_message("10.5 de dano 😂")
