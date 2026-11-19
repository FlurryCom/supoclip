import numpy as np

from src.media.nudity_check import NudityModel, merge_regions


def test_merge_regions_joins_consecutive_overlapping_boxes():
    samples = [
        (1.0, [(0.40, 0.40, 0.10, 0.10)]),
        (1.5, [(0.42, 0.41, 0.10, 0.10)]),
        (2.0, [(0.44, 0.42, 0.10, 0.10)]),
    ]
    (region,) = merge_regions(samples, step=0.5)
    # Starts one sample early: the content may have appeared any time since the previous sample.
    assert region["kind"] == "nudity" and region["start"] == 0.5 and region["end"] == 2.5
    assert (round(region["x"], 2), round(region["y"], 2)) == (0.40, 0.40)
    assert (round(region["w"], 2), round(region["h"], 2)) == (0.14, 0.12)


def test_merge_regions_keeps_separate_places_apart():
    samples = [(1.0, [(0.0, 0.0, 0.1, 0.1), (0.8, 0.8, 0.1, 0.1)]), (1.5, [(0.0, 0.0, 0.1, 0.1)])]
    regions = merge_regions(samples, step=0.5)
    assert len(regions) == 2
    assert sorted((r["start"], r["end"]) for r in regions) == [(0.5, 1.5), (0.5, 2.0)]


def test_merge_regions_closes_after_a_gap():
    samples = [(1.0, [(0.4, 0.4, 0.1, 0.1)]), (5.0, [(0.4, 0.4, 0.1, 0.1)])]
    assert len(merge_regions(samples, step=0.5)) == 2


def test_merge_regions_stays_inside_the_frame():
    (region,) = merge_regions([(0.0, [(0.95, -0.05, 0.2, 0.2)])], step=0.5)
    assert region["start"] == 0.0  # never before the clip starts
    assert 0 <= region["x"] and region["x"] + region["w"] <= 1 + 1e-9
    assert 0 <= region["y"] and region["y"] + region["h"] <= 1 + 1e-9


def test_model_loads_and_finds_nothing_on_a_blank_frame():
    assert NudityModel.get().detect(np.zeros((360, 640, 3), dtype=np.uint8)) == []
