import numpy as np
import pytest

from src.media.gore_check import contact_sheets, find_gore, gore_regions


def test_gore_regions_map_cells_to_frame_fractions():
    answer = {"gore": [{"sheet": 1, "tile": 3, "cells": [6]}]}
    assert gore_regions(answer, [[0.0, 1.0, 2.0, 3.0]]) == [
        {"kind": "gore", "start": 2.0, "end": 3.0, "x": 0.25, "y": 0.25, "w": 0.25, "h": 0.25}
    ]


def test_gore_cells_out_of_range_are_dropped():
    answer = {"gore": [
        {"sheet": 1, "tile": 99, "cells": [1]},
        {"sheet": 2, "tile": 1, "cells": [1]},
        {"sheet": 1, "tile": 1, "cells": [0, 17]},
    ]}
    assert gore_regions(answer, [[0.0, 1.0]]) == []


def test_contact_sheets_hold_16_tiles():
    frames = [(float(i), np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(20)]
    sheets = contact_sheets(frames)
    assert [len(times) for _, times in sheets] == [16, 4]
    assert all(jpeg[:2] == b"\xff\xd8" for jpeg, _ in sheets)


def test_find_gore_retries_then_raises():
    frames = [(0.0, np.zeros((360, 640, 3), dtype=np.uint8))]
    calls = []

    def always_fails(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("cli down")

    with pytest.raises(RuntimeError):
        find_gore(frames, run=always_fails)
    assert len(calls) == 3

    attempts = iter([RuntimeError("blip"), {"gore": []}])

    def flaky(*args, **kwargs):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert find_gore(frames, run=flaky) == []
