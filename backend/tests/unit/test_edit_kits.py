from src.caption_templates import get_template
from src.edit_kits import KITS, get_kit, kit_choices


def test_catclips_kit_points_at_existing_assets():
    kit = get_kit("catclips")
    assert kit["caption_template"] == "catclips"
    assert kit["end_card"].exists()
    assert [s["at"] for s in kit["sfx"]] == ["start", "peak"]
    assert all(s["path"].exists() for s in kit["sfx"])


def test_unknown_kit_is_none():
    assert get_kit("nope") is KITS["none"]
    assert get_kit(None) is KITS["none"]
    none = KITS["none"]
    assert none["chat_overlay"] is False and none["sfx"] == [] and none["end_card"] is None


def test_kit_choices_lists_every_kit():
    assert kit_choices() == [{"id": "none", "name": "SupoClip"}, {"id": "catclips", "name": "Cat Clips"}]


def test_catclips_caption_template():
    t = get_template("catclips")
    assert t["uppercase"] is True and t["emoji"] is False and t["word_box"] is True
    assert t["word_box_color"] == "#FEC803"
    assert t["highlight_color"] == "#111111"
    assert t["font_color"] == "#F3E9D2"
    assert t["stroke_color"] == "#111111"
    assert t["font_family"] == "GochiHand-Regular"
