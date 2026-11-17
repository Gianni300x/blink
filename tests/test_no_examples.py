"""Omitir evidencia de filas conserva diagnósticos y evita recolectar valores."""

from dataclasses import asdict
import json

import polars as pl
import pytest
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze


def _without_examples(value):
    if isinstance(value, dict):
        return {key: [] if key == "examples" else _without_examples(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_examples(item) for item in value]
    return value


def _frame():
    # Fixture sintética: nulos, NaN, constante, duplicados, IQR y tipo roto.
    return pl.DataFrame({
        "amount": [None, float("nan")] + list(map(float, range(38))) + [1000., 1000.],
        "text": [str(i) for i in range(40)] + ["PRIVATE_CELL_MARKER"] * 2,
        "constant": ["PRIVATE_CONSTANT_MARKER"] * 42,
    })


@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("ml", [False, True])
def test_omitting_examples_preserves_metrics_and_never_collects_values(monkeypatch, lazy, ml):
    from blink_data_lint.core import analyzer, detectors, ml as ml_module

    frame = _frame()
    source = frame.lazy() if lazy else frame
    original = analyze(source, ml=ml)
    assert any(f.examples for f in original.findings)
    assert {"nulls", "nan", "constant", "duplicates", "numeric_outliers", "broken_type"} <= {
        f.code for f in original.findings}
    if ml:
        assert next(d for d in original.detectors if d.method == "IsolationForest").examples

    def forbidden(*args, **kwargs):
        pytest.fail("No se deben recolectar ejemplos si están desactivados")

    monkeypatch.setattr(analyzer, "finding_examples", forbidden)
    monkeypatch.setattr(detectors, "series_examples", forbidden)
    monkeypatch.setattr(ml_module, "frame_examples", forbidden)
    progress = []
    redacted = analyze(source, ml=ml, include_examples=False,
                       progress=lambda stage, *args: progress.append(stage))
    assert all(not item.examples for item in (*redacted.findings, *redacted.detectors))
    assert _without_examples(asdict(original)) == _without_examples(asdict(redacted))
    assert "Buscando ejemplos" not in progress


@pytest.mark.parametrize("format", ["csv", "parquet"])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_omits_cell_values_with_ml_baseline_and_exit_code(tmp_path, format, json_output):
    frame = _frame()
    current = tmp_path / f"current.{format}"
    baseline = tmp_path / f"previous.{format}"
    write = frame.write_csv if format == "csv" else frame.write_parquet
    write(current)
    write(baseline)
    before = current.read_bytes(), baseline.read_bytes()
    args = [str(current), "--baseline", str(baseline), "--ml", "--exit-code"]
    if json_output:
        args.append("--json")
    runner = CliRunner()
    original = runner.invoke(app, args)
    redacted = runner.invoke(app, [*args, "--no-examples"])
    assert original.exit_code == redacted.exit_code == 1
    assert not original.stderr and not redacted.stderr
    assert "PRIVATE_CELL_MARKER" in original.stdout
    assert "PRIVATE_CELL_MARKER" not in redacted.stdout
    assert "PRIVATE_CONSTANT_MARKER" not in redacted.stdout
    if json_output:
        document = json.loads(redacted.stdout)
        assert document["schema_version"] == "1.1"
        assert document["comparison"] is not None
        assert all(not item["examples"] for key in ("findings", "detectors")
                   for item in document["analysis"][key])
        assert document == _without_examples(json.loads(original.stdout))
    else:
        assert "Qué revisar" in redacted.stdout
        assert "Registro 1" not in redacted.stdout
        assert "Sobre los ejemplos" not in redacted.stdout
    assert (current.read_bytes(), baseline.read_bytes()) == before


def test_no_examples_keeps_success_and_error_exit_codes(tmp_path):
    runner = CliRunner()
    empty = tmp_path / "empty.csv"
    empty.write_text("amount\n", encoding="utf-8")
    for args, expected in (([str(empty)], 0), ([str(tmp_path / "missing.csv")], 2)):
        result = runner.invoke(app, [*args, "--json", "--no-examples"])
        assert result.exit_code == expected
        assert json.loads(result.stdout)["exit_code"] == expected
