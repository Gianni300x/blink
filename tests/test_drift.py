from datetime import date
from io import StringIO
from pathlib import Path
import subprocess
import sys

import polars as pl
import pytest
from rich.console import Console
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.comparison import compare
from blink_data_lint.ui.reporter import show_comparison


def metrics(current, previous):
    return {item.method: item for item in compare(current, previous).drift}


def test_identical_reordered_and_repeated_rows_have_no_drift() -> None:
    before = pl.DataFrame({"x": list(range(100)) * 2})
    after = pl.DataFrame({"x": list(reversed(range(100))) * 3})
    results = metrics(after, before)
    assert all(item.score == pytest.approx(0) and not item.flagged for item in results.values())
    assert results["ks"].baseline_eligible == 200
    assert results["ks"].current_eligible == 300


def test_known_mean_and_ks_shifts() -> None:
    previous = pl.DataFrame({"x": list(range(100))})
    current = pl.DataFrame({"x": list(range(50, 150))})
    results = metrics(current, previous)
    assert results["mean_shift"].score == pytest.approx(50 / (9999 / 12) ** 0.5)
    assert results["mean_shift"].baseline_value == pytest.approx(49.5)
    assert results["mean_shift"].current_value == pytest.approx(99.5)
    assert results["mean_shift"].flagged
    assert results["ks"].score == pytest.approx(0.5)
    assert results["ks"].flagged


def test_changed_distribution_with_unchanged_mean() -> None:
    results = metrics(pl.DataFrame({"x": [-10., 10.] * 100}), pl.DataFrame({"x": [-1., 1.] * 100}))
    assert results["mean_shift"].score == pytest.approx(0)
    assert not results["mean_shift"].flagged
    assert results["ks"].score == pytest.approx(0.5)
    assert results["ks"].flagged


@pytest.mark.parametrize("new_value,score", [(0., 0.), (1., 1.)])
def test_constant_baseline_skips_mean_but_compares_distribution(new_value, score) -> None:
    results = metrics(pl.DataFrame({"x": [new_value] * 100}), pl.DataFrame({"x": [0.] * 100}))
    assert results["mean_shift"].score is None
    assert "cero" in results["mean_shift"].reason
    assert results["ks"].score == score
    assert results["ks"].flagged == (new_value != 0)


def test_ks_matches_reference_for_ties_and_unequal_sizes() -> None:
    from scipy.stats import ks_2samp

    previous = [i % 13 for i in range(157)]
    current = [i % 19 for i in range(233)]
    result = metrics(pl.DataFrame({"x": current}), pl.DataFrame({"x": previous}))["ks"]
    assert result.score == pytest.approx(ks_2samp(previous, current).statistic)


def test_ks_chunk_boundaries_preserve_the_exact_statistic() -> None:
    from scipy.stats import ks_2samp

    previous = [i % 90_000 for i in range(140_000)]
    current = [i % 110_000 for i in range(150_000)]
    result = metrics(pl.DataFrame({"x": current}), pl.DataFrame({"x": previous}))["ks"]
    assert result.score == pytest.approx(ks_2samp(previous, current, method="asymp").statistic, abs=1e-15)


@pytest.mark.parametrize("shift,expected", [(0.49, False), (0.5, True), (0.51, True)])
def test_mean_threshold(shift, expected) -> None:
    result = metrics(pl.DataFrame({"x": [-1. + shift, 1. + shift] * 100}),
                     pl.DataFrame({"x": [-1., 1.] * 100}))["mean_shift"]
    assert result.score == pytest.approx(shift)
    assert result.flagged is expected


@pytest.mark.parametrize("shift,expected", [(19, False), (20, True), (21, True)])
def test_ks_threshold(shift, expected) -> None:
    result = metrics(pl.DataFrame({"x": list(range(shift, 100 + shift))}),
                     pl.DataFrame({"x": list(range(100))}))["ks"]
    assert result.score == pytest.approx(shift / 100)
    assert result.flagged is expected


@pytest.mark.parametrize("new_a,expected", [(61, False), (60, True), (59, True)])
def test_category_frequency_threshold_and_new_categories(new_a, expected) -> None:
    result = metrics(pl.DataFrame({"group": ["A"] * new_a + ["C"] * (100 - new_a)}),
                     pl.DataFrame({"group": ["A"] * 80 + ["C"] * 20}))["total_variation"]
    assert result.score == pytest.approx(abs(new_a / 100 - 0.8))
    assert result.flagged is expected
    completely_new = metrics(pl.DataFrame({"group": ["B"] * 100}),
                             pl.DataFrame({"group": ["A"] * 100}))["total_variation"]
    assert completely_new.score == 1


def test_boolean_distribution_and_different_row_counts() -> None:
    result = metrics(pl.DataFrame({"flag": [True] * 50 + [False] * 150}),
                     pl.DataFrame({"flag": [True] * 75 + [False] * 25}))["total_variation"]
    assert result.score == 0.5 and result.flagged


def test_categorical_frequencies_not_counts_or_order() -> None:
    result = metrics(pl.DataFrame({"group": [""] * 150 + ["B"] * 150}),
                     pl.DataFrame({"group": ["B"] * 50 + [""] * 50}))["total_variation"]
    assert result.score == 0 and not result.flagged


def test_category_limit_is_on_union_and_ids_are_omitted() -> None:
    result = metrics(pl.DataFrame({"group": [f"cat{i % 50}" for i in range(200)]}),
                     pl.DataFrame({"group": [f"cat{i % 50}" for i in range(200)]}))["total_variation"]
    assert result.score == 0
    result = metrics(pl.DataFrame({"group": [f"other{i % 50}" for i in range(200)]}),
                     pl.DataFrame({"group": [f"cat{i % 50}" for i in range(200)]}))["total_variation"]
    assert "50 categorías" in result.reason
    result = metrics(pl.DataFrame({"order_id": range(100, 200)}),
                     pl.DataFrame({"order_id": range(100)}))
    assert "50 categorías" in result["total_variation"].reason
    assert not result["total_variation"].flagged


@pytest.mark.parametrize("null_count,expected", [(29, False), (30, True), (31, True)])
def test_missing_percentage_point_threshold(null_count, expected) -> None:
    previous = pl.DataFrame({"x": [None] * 20 + [1.] * 80})
    current = pl.DataFrame({"x": [None] * null_count + [1.] * (100 - null_count)})
    result = metrics(current, previous)["missing_rate"]
    assert result.score == pytest.approx(abs(null_count / 100 - 0.2))
    assert result.flagged is expected


def test_null_nan_inf_have_explicit_coverage() -> None:
    frame = pl.DataFrame({"x": list(map(float, range(100))) + [None, float("nan"), float("inf")]})
    results = metrics(frame, frame)
    assert results["ks"].baseline_eligible == results["ks"].current_eligible == 100
    assert results["ks"].baseline_rows == 103
    assert results["missing_rate"].baseline_value == pytest.approx(2 / 103)
    assert all(not result.flagged for result in results.values())


@pytest.mark.parametrize("count", [0, 99])
def test_insufficient_observations_are_not_a_zero_score(count) -> None:
    frame = pl.DataFrame({"x": list(range(count))}, schema={"x": pl.Int64})
    results = metrics(frame, frame)
    assert all(result.score is None and result.reason and not result.flagged for result in results.values())


def test_all_null_and_unsupported_types() -> None:
    all_null = pl.DataFrame({"x": [None] * 100}, schema={"x": pl.Float64})
    results = metrics(all_null, all_null)
    assert results["missing_rate"].score == 0
    assert results["ks"].score is None
    native_dates = pl.DataFrame({"date": [date(2026, 1, 1)] * 100})
    result = metrics(native_dates, native_dates)["distribution"]
    assert result.reason and result.score is None


def test_type_changes_are_not_silently_coerced() -> None:
    current, previous = pl.DataFrame({"x": [1.] * 100}), pl.DataFrame({"x": [1] * 100})
    result = compare(current, previous)
    assert result.changes[0].kind == "type_changed"
    assert len(result.drift) == 1
    assert "Tipos diferentes" in result.drift[0].reason


def test_extreme_and_precision_sensitive_values() -> None:
    frame = pl.DataFrame({"x": [-1e308, 1e308] * 100})
    result = metrics(frame, frame)
    assert result["mean_shift"].score == 0 and result["ks"].score == 0
    previous = pl.DataFrame({"x": [2**53 + 1] * 100})
    current = pl.DataFrame({"x": [2**53 + 2] * 100})
    assert "precisión" in metrics(current, previous)["ks"].reason


def test_scaled_precision_loss_does_not_hide_distribution_change() -> None:
    previous = pl.DataFrame({"x": [1e-320, 2e-320] * 100})
    current = pl.DataFrame({"x": [1e308, -1e308] * 100})
    results = metrics(current, previous)
    assert results["mean_shift"].score is None
    assert "pierde valores" in results["mean_shift"].reason
    assert results["ks"].score == 0.5
    assert results["ks"].flagged


@pytest.mark.parametrize("current_format,baseline_format", [("csv", "csv"), ("parquet", "parquet"), ("csv", "parquet"), ("parquet", "csv")])
def test_cli_real_drift_and_inputs_unchanged(tmp_path: Path, current_format, baseline_format) -> None:
    previous = tmp_path / f"previous.{baseline_format}"
    current = tmp_path / f"current.{current_format}"
    getattr(pl.DataFrame({"amount": range(100)}), f"write_{baseline_format}")(previous)
    getattr(pl.DataFrame({"amount": range(100, 200)}), f"write_{current_format}")(current)
    original = current.read_bytes(), previous.read_bytes()
    result = CliRunner().invoke(app, [str(current), "--baseline", str(previous)])
    assert result.exit_code == 0, result.output
    assert "AVISO · CAMBIO DE MEDIA" in result.output
    assert "AVISO · DISTRIBUCIÓN KS" in result.output
    assert "Sin cambios de nombres o tipos" in result.output
    assert (current.read_bytes(), previous.read_bytes()) == original


@pytest.mark.parametrize("width", [40, 80, 120])
def test_report_does_not_expose_categories_and_shows_coverage(width) -> None:
    report = compare(pl.DataFrame({"[red]group": ["SECRET_NEW"] * 100}),
                     pl.DataFrame({"[red]group": ["SECRET_OLD"] * 100}))
    stream = StringIO()
    show_comparison(report, Path("previous.csv"), console=Console(file=stream, width=width, color_system=None))
    text = stream.getvalue()
    assert "REVISAR" in text and "AVISO" in text
    assert "Elegibles" in text and "100/100" in text
    assert "[red]" in text
    assert "SECRET_" not in text and "\x1b" not in text
    assert all(len(line) <= width for line in text.splitlines())


def test_basic_analysis_still_does_not_import_ml_or_numpy() -> None:
    code = ("import polars as pl; from blink_data_lint.core.analyzer import analyze; import sys; "
            "analyze(pl.DataFrame({'x': range(100)})); "
            "assert 'numpy' not in sys.modules; assert 'sklearn' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)
