from supoclip_live.service import fair_share


def test_one_account_gets_every_slot():
    assert fair_share(3, 1) == 3


def test_slots_are_shared_rounding_up():
    assert fair_share(3, 2) == 2
    assert fair_share(3, 3) == 1


def test_every_account_gets_at_least_one():
    assert fair_share(3, 10) == 1
    assert fair_share(3, 0) == 3


def test_window_cap_follows_the_channel_switch():
    from supoclip_live.service import window_cap
    assert window_cap({"limit_windows": True, "daily_window_cap": 48}) == 48
    assert window_cap({"limit_windows": False, "daily_window_cap": 48}) > 10_000  # off: no daily limit
