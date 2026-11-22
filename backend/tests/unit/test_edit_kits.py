from src.caption_templates import get_template
from src.edit_kits import KITS, get_kit, kit_choices


def test_catclips_kit_points_at_existing_assets():
    kit = get_kit("catclips")
    assert kit["caption_template"] == "catclips"
    assert kit["end_card"] is None and kit["cta_seconds"] == 2.5
    assert [(s["path"].name, s["at"]) for s in kit["sfx"]] == [
        ("bell.wav", "start"), ("cartoon-whoosh.wav", "start"), ("boom.wav", "peak")]
    assert all(s["path"].exists() for s in kit["sfx"])
    assert kit["chat_overlay"] is True and kit["burn_hook_title"] is True


def test_catclips_v2_keeps_the_look_without_text_over_the_video():
    v1, v2 = get_kit("catclips"), get_kit("catclips_v2")
    assert v2["name"] == "Cat Clips v2"
    assert v2["chat_overlay"] is False and v2["burn_hook_title"] is False
    assert v2["layout"] == "streamer_split" and v1["layout"] is None
    assert v2["sfx"] == v1["sfx"] and v2["caption_template"] == "catclips" and v2["cta_seconds"] == 2.5


def test_unknown_kit_is_none():
    assert get_kit("nope") is KITS["none"]
    assert get_kit(None) is KITS["none"]
    none = KITS["none"]
    assert none["chat_overlay"] is False and none["sfx"] == [] and none["end_card"] is None
    assert none["cta_seconds"] == 0 and none["burn_hook_title"] is True


def test_kit_choices_lists_every_kit():
    assert kit_choices() == [{"id": "none", "name": "SupoClip"}, {"id": "catclips", "name": "Cat Clips"},
                            {"id": "catclips_v2", "name": "Cat Clips v2"}]


def test_catclips_caption_template():
    t = get_template("catclips")
    assert t["uppercase"] is True and t["emoji"] is False and t["word_box"] is True
    assert t["word_box_color"] == "#FEC803"
    assert t["highlight_color"] == "#111111"
    assert t["font_color"] == "#F3E9D2"
    assert t["stroke_color"] == "#111111"
    assert t["font_family"] == "ArchivoBlack-Regular"


def test_sfx_files_are_short_and_loud_enough():
    import wave
    import numpy as np
    from src.edit_kits import ASSETS_DIR
    for name, longest in (("bell.wav", 2.5), ("boom.wav", 3.0), ("click.wav", 2.5), ("pop.wav", 2.5)):
        with wave.open(str(ASSETS_DIR / "sfx" / name)) as w:
            assert w.getframerate() == 48000 and w.getnchannels() == 1, name
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768
            assert w.getnframes() / 48000 <= longest, name
        assert 0.3 <= np.abs(samples).max() <= 1.0, name


def test_v2_kit_splits_streamer_friend_and_game_captions():
    # Owner approved the real preview (2026-11-20): on for every v2 channel.
    assert get_kit("catclips_v2")["voice_roles"] is True
    assert get_kit("catclips")["voice_roles"] is False and get_kit("none")["voice_roles"] is False


def test_kits_music_clean_off_until_approved():
    # Owner sees a real before/after first; then catclips and catclips_v2 turn it on.
    assert get_kit("none")["music_clean"] is False
    assert get_kit("catclips")["music_clean"] is False and get_kit("catclips_v2")["music_clean"] is False
