from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import polars as pl
from polars.exceptions import ComputeError
import pytest
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.summary import summarize
from blink_data_lint.io.serializer import serialize_report

runner = CliRunner()
TOP_LEVEL_KEYS = {"schema_version", "status", "sources", "analysis", "comparison", "summary", "error", "exit_code"}


def read_document(result):
    assert "\x1b" not in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert set(document) == TOP_LEVEL_KEYS
    assert document["schema_version"] == "1.0"
    assert document["exit_code"] == result.exit_code
    return document


def write_frame(tmp_path, name="current", values=None):
    path = tmp_path / f"{name}.parquet"
    pl.DataFrame({"x": list(range(100)) if values is None else values}).write_parquet(path)
    return path


@pytest.mark.parametrize("options,expected", [([], 0), (["--json"], 0), (["--exit-code"], 1), (["--json", "--exit-code"], 1)])
def test_warnings_fail_only_when_enabled(tmp_path, options, expected):
    path = write_frame(tmp_path, values=[1, 1, None])
    original = path.read_bytes()
    result = runner.invoke(app, [str(path), *options])
    assert result.exit_code == expected, result.output
    assert path.read_bytes() == original
    if "--json" in options:
        document = read_document(result)
        assert document["status"] == "completed"
        assert document["summary"]["warnings"] > 0
        assert document["error"] is None
        assert result.stderr == ""


def test_clean_json_contract_and_completed_detectors(tmp_path):
    path = write_frame(tmp_path)
    result = runner.invoke(app, [str(path), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 0
    assert result.stderr == ""
    assert document["sources"]["current"]["path"] == str(path)
    assert document["sources"]["current"]["format"] == "Parquet"
    assert document["sources"]["baseline"] is None
    assert document["comparison"] is None
    assert document["analysis"]["rows"] == 100
    assert document["summary"]["warnings"] == 0
    detector = document["analysis"]["detectors"][0]
    assert detector["method"] == "IQR"
    assert detector["status"] == "completed" and detector["count"] == 0
    assert detector["parameters"]["factor"] == "1.5"
    skipped = document["analysis"]["detectors"][1]
    assert skipped["status"] == "skipped" and skipped["count"] is None
    assert skipped["reason"]
    assert document["summary"]["skipped_analyses"] == 1


def test_information_and_omissions_do_not_fail_ci(tmp_path):
    path = tmp_path / "info.csv"
    pl.DataFrame({"id": [f"u{i}" for i in range(20)]}).write_csv(path)
    result = runner.invoke(app, [str(path), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 0
    assert document["summary"] == {"warnings": 0, "information": 1, "skipped_analyses": 2}


def test_small_dataset_exposes_skipped_without_claiming_zero(tmp_path):
    path = write_frame(tmp_path, values=range(9))
    result = runner.invoke(app, [str(path), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 0
    assert document["summary"]["skipped_analyses"] == 2
    assert all(item["status"] == "skipped" and item["count"] is None for item in document["analysis"]["detectors"])


@pytest.mark.parametrize("options,expected", [(["--json"], 0), (["--json", "--exit-code"], 1)])
def test_drift_warnings_and_flag_properties_are_serialized(tmp_path, options, expected):
    previous = write_frame(tmp_path, "previous")
    current = write_frame(tmp_path, values=range(50, 150))
    originals = current.read_bytes(), previous.read_bytes()
    result = runner.invoke(app, [str(current), "--baseline", str(previous), *options])
    document = read_document(result)
    assert result.exit_code == expected
    assert document["analysis"]["findings"] == []
    assert document["summary"]["warnings"] == 2
    assert document["comparison"]["changes"] == []
    drift = {metric["method"]: metric for metric in document["comparison"]["drift"]}
    assert drift["ks"]["status"] == "completed" and drift["ks"]["flagged"]
    assert drift["ks"]["score"] == pytest.approx(0.5)
    assert document["sources"]["baseline"]["path"] == str(previous)
    assert (current.read_bytes(), previous.read_bytes()) == originals


def test_schema_changes_fail_ci_and_drift_is_skipped(tmp_path):
    previous = write_frame(tmp_path, "previous")
    current = tmp_path / "current.csv"
    pl.DataFrame({"x": [f"u{i}" for i in range(100)], "added": range(100)}).write_csv(current)
    result = runner.invoke(app, [str(current), "--baseline", str(previous), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 1
    assert document["summary"]["warnings"] == 2
    assert {item["kind"] for item in document["comparison"]["changes"]} == {"added", "type_changed"}
    assert document["comparison"]["drift"][0]["status"] == "skipped"
    assert document["comparison"]["drift"][0]["score"] is None
    assert not document["comparison"]["drift"][0]["flagged"]


def test_baseline_health_warnings_are_not_added_to_current_summary(tmp_path):
    previous = write_frame(tmp_path, "previous", values=[None] + list(range(1, 100)))
    current = write_frame(tmp_path)
    result = runner.invoke(app, [str(current), "--baseline", str(previous), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 0 and document["summary"]["warnings"] == 0


def test_json_ml_preserves_seed_coverage_and_no_values(tmp_path):
    path = write_frame(tmp_path, values=[0.] * 200 + [10000.])
    result = runner.invoke(app, [str(path), "--ml", "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 1
    ml = document["analysis"]["detectors"][-1]
    assert ml["method"] == "IsolationForest" and ml["status"] == "completed"
    assert ml["count"] == 1 and ml["evaluated_rows"] == ml["eligible_rows"] == 201
    assert ml["parameters"]["seed"] == "42"


@pytest.mark.parametrize("kind", ["missing", "directory", "unsupported", "corrupt", "encoding"])
@pytest.mark.parametrize("baseline_error", [False, True])
def test_json_read_errors_are_structured_and_sanitized(tmp_path, kind, baseline_error):
    good = write_frame(tmp_path)
    bad = tmp_path / "bad.csv"
    if kind == "directory":
        bad.mkdir()
    elif kind == "unsupported":
        bad = tmp_path / "bad.txt"
        bad.write_text("PRIVATE_CELL")
    elif kind == "corrupt":
        bad.write_text("a,b\nPRIVATE_CELL,2,3\n")
    elif kind == "encoding":
        bad.write_bytes(b"a\n\xff\n")
    args = [str(good), "--baseline", str(bad)] if baseline_error else [str(bad)]
    result = runner.invoke(app, [*args, "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 2
    assert document["status"] == "error" and document["error"]["code"] == "read_error"
    assert document["error"]["stage"] == ("baseline" if baseline_error else "current")
    assert document["analysis"] is document["comparison"] is document["summary"] is None
    assert "Error" in result.stderr
    assert "PRIVATE_CELL" not in result.stdout + result.stderr


def test_json_analysis_failure_has_no_partial_success(tmp_path, monkeypatch):
    path = write_frame(tmp_path)

    def fail(*args, **kwargs):
        raise ComputeError("PRIVATE_CELL")

    monkeypatch.setattr("blink_data_lint.cli.analyze", fail)
    result = runner.invoke(app, [str(path), "--json"])
    document = read_document(result)
    assert result.exit_code == 2
    assert document["error"]["code"] == "analysis_error"
    assert document["analysis"] is None
    assert "PRIVATE_CELL" not in result.stdout + result.stderr


@pytest.mark.parametrize("error", [ValueError, OverflowError])
def test_numeric_failures_are_sanitized_in_json(tmp_path, monkeypatch, error):
    path = write_frame(tmp_path)

    def fail(*args, **kwargs):
        raise error("PRIVATE_CELL")

    monkeypatch.setattr("blink_data_lint.cli.analyze", fail)
    result = runner.invoke(app, [str(path), "--json"])
    document = read_document(result)
    assert result.exit_code == 2 and document["error"]["code"] == "analysis_error"
    assert "PRIVATE_CELL" not in result.stdout + result.stderr


def test_nonfinite_report_fails_as_structured_error_without_partial_json(tmp_path, monkeypatch):
    path = write_frame(tmp_path)
    invalid_report = replace(analyze(pl.DataFrame({"x": range(100)})), null_percent=float("inf"))
    monkeypatch.setattr("blink_data_lint.cli.analyze", lambda *args, **kwargs: invalid_report)
    result = runner.invoke(app, [str(path), "--json"])
    document = read_document(result)
    assert result.exit_code == 2
    assert document["error"]["code"] == "serialization_error"
    assert document["analysis"] is None


def test_empty_dataset_is_a_warning_and_undefined_percent_is_json_null(tmp_path):
    path = write_frame(tmp_path, values=[])
    result = runner.invoke(app, [str(path), "--json", "--exit-code"])
    document = read_document(result)
    assert result.exit_code == 1
    assert document["analysis"]["null_percent"] is None
    assert document["summary"]["warnings"] > 0


def test_nonfinite_cells_never_become_json_nan_or_infinity(tmp_path):
    path = write_frame(tmp_path, values=[None, float("nan"), float("inf"), -float("inf")] + list(map(float, range(100))))
    result = runner.invoke(app, [str(path), "--json"])
    document = read_document(result)
    assert result.exit_code == 0
    assert document["analysis"]["nan_count"] == 1
    # Reject nonstandard numeric constants while accepting human messages containing NaN.
    json.loads(result.stdout, parse_constant=lambda value: pytest.fail(f"Nonstandard JSON number: {value}"))


def test_serializer_rejects_nonfinite_metrics_instead_of_emitting_invalid_json():
    report = replace(analyze(pl.DataFrame({"x": range(20)})), null_percent=float("nan"))
    with pytest.raises(ValueError):
        serialize_report(report, None, summarize(report), sources={}, exit_code=0)


def test_unicode_and_markup_paths_are_json_data(tmp_path):
    path = tmp_path / "[red]datos ñ.csv"
    pl.DataFrame({"[red]columna": range(20)}).write_csv(path)
    result = runner.invoke(app, [str(path), "--json"])
    document = read_document(result)
    assert document["sources"]["current"]["path"] == str(path)
    assert document["analysis"]["columns"][0]["name"] == "[red]columna"


def test_json_does_not_disclose_categorical_values(tmp_path):
    previous = tmp_path / "previous.csv"
    current = tmp_path / "current.csv"
    pl.DataFrame({"group": ["PRIVATE_OLD"] * 100}).write_csv(previous)
    pl.DataFrame({"group": ["PRIVATE_NEW"] * 100}).write_csv(current)
    result = runner.invoke(app, [str(current), "--baseline", str(previous), "--json"])
    document = read_document(result)
    assert document["comparison"]["drift"][-1]["flagged"]
    assert "PRIVATE_OLD" not in result.stdout and "PRIVATE_NEW" not in result.stdout


@pytest.mark.parametrize("arguments", [["--json"], ["--json", "--unknown"], ["data.csv", "--json", "--baseline"]])
def test_cli_syntax_errors_do_not_emit_a_partial_document(arguments):
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr and "Traceback" not in result.stderr


def test_shell_redirected_json_is_parseable_with_exit_one(tmp_path):
    path = write_frame(tmp_path, values=[1, 1, None])
    output = tmp_path / "report.json"
    with output.open("w") as stream:
        result = subprocess.run([sys.executable, "-m", "blink_data_lint.cli", str(path), "--json", "--exit-code"],
                                stdout=stream, stderr=subprocess.PIPE, text=True)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(output.read_text())["summary"]["warnings"] > 0
