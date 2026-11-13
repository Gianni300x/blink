import polars as pl
import pytest

from blink.core.analyzer import analyze


def test_known_metrics() -> None:
    frame = pl.DataFrame({
        "text_id": [f"u{i}" for i in range(20)] + ["u0"],
        "constant": [7] * 21,
        "partial": [None] + [1] * 20,
        "empty": [None] * 21,
    })
    # Hacer la última fila idéntica a la primera.
    frame = frame.with_columns(pl.Series("partial", [None] + [1] * 19 + [None]))
    result = analyze(frame)
    cols = {col.name: col for col in result.columns}
    assert result.rows == 21
    assert result.duplicate_count == 1
    assert result.null_count == 23
    assert result.null_percent == pytest.approx(23 / 84 * 100)
    assert cols["text_id"].possible_id
    assert cols["text_id"].distinct_count == 20
    assert cols["constant"].constant
    assert cols["partial"].constant
    assert cols["partial"].null_percent == pytest.approx(2 / 21 * 100)
    assert cols["empty"].all_null
    assert not cols["empty"].constant


def test_duplicate_count_is_excess_not_all_members() -> None:
    report = analyze(pl.DataFrame({"a": [1, 1, 1, 2, 2, None, None]}))
    assert report.duplicate_count == 4


def test_nan_is_separate_from_null_and_not_a_constant_value() -> None:
    result = analyze(pl.DataFrame({"x": [None, float("nan"), 2.0, 2.0]}))
    col = result.columns[0]
    assert result.null_count == 1
    assert result.nan_count == 1
    assert result.null_percent == 25
    assert col.constant
    assert col.distinct_count == 1
    assert not col.all_null


def test_all_nan_is_not_constant_or_all_null() -> None:
    result = analyze(pl.DataFrame({"x": [float("nan")] * 3}))
    col = result.columns[0]
    assert result.nan_count == 3
    assert not col.constant
    assert not col.all_null
    assert result.duplicate_count == 2


@pytest.mark.parametrize("frame", [pl.DataFrame(), pl.DataFrame(schema={"x": pl.Int64})])
def test_empty_is_not_healthy_and_percent_is_undefined(frame: pl.DataFrame) -> None:
    result = analyze(frame)
    assert result.null_percent is None
    assert result.duplicate_percent is None
    assert result.duplicate_count == 0
    assert result.findings[0].code == "empty_dataset"
    assert not any(col.constant or col.all_null for col in result.columns)


@pytest.mark.parametrize(("values", "expected"), [
    ([f"u{i}" for i in range(9)], False),
    ([f"u{i}" for i in range(10)], True),
    ([f"u{i}" for i in range(19)] + ["u0"], True),
    ([f"u{i}" for i in range(18)] + ["u0", "u1"], False),
    (["", None] * 20, False),
])
def test_id_boundaries(values: list[str | None], expected: bool) -> None:
    assert analyze(pl.DataFrame({"id": values})).columns[0].possible_id is expected


def test_numeric_ids_are_not_flagged_as_text_ids() -> None:
    assert not analyze(pl.DataFrame({"id": range(20)})).columns[0].possible_id


def test_empty_string_is_observed_and_infinity_is_not_missing() -> None:
    result = analyze(pl.DataFrame({"text": ["", ""], "float": [float("inf")] * 2}))
    assert result.null_count == result.nan_count == 0
    assert all(col.constant for col in result.columns)
