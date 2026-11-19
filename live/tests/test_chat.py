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
