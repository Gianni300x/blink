import math
import random

import polars as pl
import pytest

from blink_data_lint.core.duplicates import excess_duplicates


@pytest.mark.parametrize("frame", [
    pl.DataFrame(), pl.DataFrame(schema={"x": pl.Int64}),
    pl.DataFrame({"x": [1, 2, 3]}), pl.DataFrame({"x": [1, 1, 1]}),
    pl.DataFrame({"x": [None, None, 1, 1, 2]}),
    pl.DataFrame({"x": [float("nan"), math.nan, -0., 0., float("inf"), float("inf")]}),
    pl.DataFrame({"x": [None] * 20}),
    pl.DataFrame({"x": ["", "", None, None, "a"]}),
    pl.DataFrame({"x": [1, 1, 2, 2], "y": [True, False, True, True]}),
])
def test_matches_exact_reference(frame):
    expected = frame.height - frame.n_unique() if frame.height and frame.width else 0
    assert excess_duplicates(frame) == expected


def test_seeded_mixed_rows_match_reference():
    rng = random.Random(42)
    frame = pl.DataFrame({
        "x": [rng.choice([None, -0., 0., 1., float("nan"), float("inf")]) for _ in range(1000)],
        "text": [rng.choice([None, "", "A", "B"]) for _ in range(1000)],
        "flag": [rng.choice([None, True, False]) for _ in range(1000)],
    })
    assert excess_duplicates(frame) == frame.height - frame.n_unique()


def test_full_hash_collision_cannot_create_false_duplicates(monkeypatch):
    frame = pl.DataFrame({"x": [1, 2, 3, 3]})
    monkeypatch.setattr(pl.DataFrame, "hash_rows", lambda self, **kwargs: pl.Series([0] * self.height))
    assert excess_duplicates(frame) == 1


def test_partial_hash_collision_verifies_values(monkeypatch):
    frame = pl.DataFrame({"x": [1, 2, 3, 3, 4]})
    monkeypatch.setattr(pl.DataFrame, "hash_rows", lambda self, **kwargs: pl.Series([0, 0, 1, 1, 2]))
    assert excess_duplicates(frame) == 1
