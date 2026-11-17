from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import subprocess
import sys

import polars as pl
import pytest
from rich.console import Console
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.detectors import broken_type, isolation_forest, numeric_outliers
from blink_data_lint.ui.reporter import show_report


def test_iqr_known_outlier_and_boundaries() -> None:
    result = numeric_outliers(pl.Series("amount", list(range(20)) + [1000]))
    assert result.count == 1
    assert result.evaluated_rows == result.eligible_rows == 21
    params = dict(result.parameters)
    assert float(params["inferior"]) == -10
    assert float(params["superior"]) == 30
    boundary = numeric_outliers(pl.Series("x", [-10] + list(range(21)) + [30]))
    assert boundary.count == 0


@pytest.mark.parametrize("values,reason", [
    ([1.0] * 20, "IQR cero"),
    ([0.0] * 19 + [100.0], "IQR cero"),
    ([1.0, None, float("nan"), float("inf"), -float("inf")], "Menos de 10"),
    ([], "Menos de 10"),
])
def test_iqr_skips_explicitly(values: list, reason: str) -> None:
    result = numeric_outliers(pl.Series("x", values, dtype=pl.Float64))
    assert reason in result.reason
    assert result.count is None
    assert result.evaluated_rows == 0


def test_iqr_excludes_only_nonfinite_and_null() -> None:
    result = numeric_outliers(pl.Series("x", list(map(float, range(20))) + [1000., None, float("nan"), float("inf")]))
    assert (result.total_rows, result.eligible_rows, result.evaluated_rows, result.count) == (24, 21, 21, 1)


def test_large_finite_values_do_not_crash_or_overflow() -> None:
    result = numeric_outliers(pl.Series("x", [-1e308, 1e308] * 10))
    assert "fuera del rango" in result.reason
    result = isolation_forest(pl.DataFrame({"x": [-1e308, 1e308] * 32}), enabled=True)
    assert result.reason is None


def test_possible_integer_ids_are_skipped() -> None:
    frame = pl.DataFrame({"customer_id": range(100)})
    assert "Posible ID" in numeric_outliers(frame["customer_id"]).reason
    assert "Sin columnas" in isolation_forest(frame, enabled=True).reason


def test_broken_numeric_preserves_evidence_and_counts() -> None:
    series = pl.Series("amount", [str(i) for i in range(18)] + ["N/A", "error", None, "", " "])
    original = series.clone()
    result = broken_type(series)
    assert (result.total_rows, result.eligible_rows, result.evaluated_rows, result.count) == (23, 20, 20, 2)
    assert dict(result.parameters)["compatibles"] == "18"
    assert series.equals(original)


def test_broken_iso_dates_calendar_validation() -> None:
    values = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(18)]
    result = broken_type(pl.Series("date", values + ["2026-02-30", "error"]))
    assert result.count == 2
    assert dict(result.parameters)["tipo candidato"] == "fecha ISO YYYY-MM-DD"


@pytest.mark.parametrize("values,reason", [
    (["01/02/2026"] * 18 + ["error", "N/A"], "Sin mayoría"),
    ([f"{i:04d}" for i in range(18)] + ["error", "N/A"], "ceros iniciales"),
    ([str(i) for i in range(17)] + ["error"] * 3, "Sin mayoría"),
    ([None, "", " "] * 10, "Menos de 10"),
    (["ordinary text"] * 20, "Sin mayoría"),
])
def test_type_ambiguity_and_insufficient_data_are_not_alerts(values: list, reason: str) -> None:
    result = broken_type(pl.Series("text", values, dtype=pl.String))
    assert reason in result.reason
    assert result.count is None


def test_comma_number_text_explains_omission_without_reinterpreting_values():
    result = broken_type(pl.Series("measurement", ["12,34"] * 7 + ["-200"] * 2 + ["missing"]))
    assert result.reason is not None and "punto decimal" in result.reason
    assert result.count is None and result.evaluated_rows == 0


def test_compatible_text_is_not_broken() -> None:
    assert broken_type(pl.Series("text", [str(i) for i in range(20)])).count == 0


def test_ml_reproducible_known_extreme_and_missing_rows() -> None:
    frame = pl.DataFrame({"x": [float(i % 10) for i in range(200)] + [10000., None, float("nan"), float("inf")]})
    result = isolation_forest(frame, enabled=True)
    assert result == isolation_forest(frame, enabled=True)
    assert result.total_rows == 204
    assert result.evaluated_rows == result.eligible_rows == 201
    # Probar el extremo por separado con el mismo contrato del detector:
    # una nube constante y un extremo deben incluir al menos una anomalía.
    isolated = isolation_forest(pl.DataFrame({"x": [0.] * 200 + [10000.]}), enabled=True)
    assert isolated.count == 1
    assert result.count > 0


def test_ml_sample_has_explicit_coverage_and_limits() -> None:
    frame = pl.DataFrame({"x": [i % 100 for i in range(12_000)]})
    result = isolation_forest(frame, enabled=True)
    assert result == isolation_forest(frame, enabled=True)
    assert (result.total_rows, result.eligible_rows, result.evaluated_rows) == (12_000, 12_000, 10_000)
    assert dict(result.parameters)["seed"] == "42"
    wide = pl.DataFrame({f"c{i}": range(100) for i in range(33)})
    assert "Más de 32" in isolation_forest(wide, enabled=True).reason


def test_ml_missing_complete_rows_and_constants() -> None:
    assert "Sin columnas" in isolation_forest(pl.DataFrame({"x": [1.] * 100}), enabled=True).reason
    frame = pl.DataFrame({"x": list(range(20)) + [None] * 20,
                          "y": [None] * 20 + list(range(20))})
    result = isolation_forest(frame, enabled=True)
    assert result.eligible_rows == 0
    assert "32 filas completas" in result.reason


def test_basic_analysis_does_not_import_numpy_or_sklearn() -> None:
    code = ("import polars as pl; from blink_data_lint.core.analyzer import analyze; import sys; "
            "analyze(pl.DataFrame({'x': range(100)})); "
            "assert 'sklearn' not in sys.modules; assert 'numpy' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_cli_detectors_read_only_and_same_metrics(tmp_path: Path, suffix: str) -> None:
    frame = pl.DataFrame({"amount": list(range(99)) + [10000],
                          "broken": [str(i % 10) for i in range(99)] + ["error"]})
    path = tmp_path / f"data.{suffix}"
    getattr(frame, f"write_{suffix}")(path)
    original = path.read_bytes()
    output = CliRunner().invoke(app, [str(path), "--ml"])
    assert output.exit_code == 0, output.output
    assert "IsolationForest" in output.output
    assert "IQR" in output.output
    assert "tipo textual" in output.output
    assert path.read_bytes() == original
    loaded = getattr(pl, f"read_{suffix}")(path)
    assert analyze(loaded, ml=True).detectors == analyze(frame, ml=True).detectors


@pytest.mark.parametrize("width", [40, 80, 120])
def test_detector_report_narrow_and_without_color(width: int) -> None:
    frame = pl.DataFrame({"[red]amount": list(range(99)) + [10000]})
    stream = StringIO()
    show_report(analyze(frame, ml=True), Path("data.csv"), format="CSV", policy="Prueba",
                console=Console(file=stream, width=width, color_system=None))
    output = stream.getvalue()
    assert "evaluadas" in output
    assert "elegibles" in output
    assert "IsolationForest" in output
    assert "[red]" in output
    assert "\x1b" not in output
    assert all(len(line) <= width for line in output.splitlines())
