"""Pruebas de regresión para los hallazgos de la auditoría."""

import json
import re

import polars as pl
import pytest
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.detectors import isolation_forest, numeric_outliers
from blink_data_lint.core.drift import detect_drift
from blink_data_lint.io.loader import load_dataset


def test_csv_with_cr_only_preserves_rows_and_columns(tmp_path):
    path = tmp_path / "cr.csv"
    path.write_bytes(b"a,b\r1,2\r3,4\r")
    invocation = CliRunner().invoke(app, [str(path), "--json"])
    assert invocation.exit_code == 0
    report = json.loads(invocation.stdout)["analysis"]
    assert (report["rows"], [column["name"] for column in report["columns"]]) == (2, ["a", "b"])


def test_tsv_with_cr_delimiter_preserves_newlines_inside_quoted_values(tmp_path):
    path = tmp_path / "legacy.tsv"
    path.write_bytes(b'a\tb\r1\t"inside\rline"\r2\t"inside\nline"\r')
    loaded = load_dataset(path)
    try:
        assert loaded.frame.to_dict(as_series=False) == {"a": [1, 2], "b": ["inside\rline", "inside\nline"]}
    finally:
        loaded.close()


@pytest.mark.parametrize("suffix,separator", [("csv", b","), ("tsv", b"\t")])
def test_short_record_is_rejected_past_delimiter_sample(tmp_path, suffix, separator):
    path = tmp_path / f"ventas.{suffix}"
    path.write_bytes(b"a" + separator + b"b\n" + (b"1" + separator + b"2\n") * 32767 + b"3\n")
    invocation = CliRunner().invoke(app, [str(path), "--json"])
    assert invocation.exit_code == 2
    document = json.loads(invocation.stdout)
    assert document["status"] == "error" and document["analysis"] is None


def test_baseline_with_extra_field_reports_read_error(tmp_path):
    current = tmp_path / "actual.csv"
    previous = tmp_path / "anterior.csv"
    current.write_bytes(b"a,b\n1,2\n3,4\n")
    previous.write_bytes(b"a,b\n" + b"1,2\n" * 33000 + b"3,4,5\n")
    invocation = CliRunner().invoke(app, [str(current), "--baseline", str(previous), "--json", "--exit-code"])
    assert invocation.exit_code == 2
    document = json.loads(invocation.stdout)
    assert document["error"]["code"] == "read_error" and document["error"]["stage"] == "baseline"


@pytest.mark.parametrize("header", ["*", "^x$"])
def test_literal_column_names_do_not_expand_as_selectors(tmp_path, header):
    path = tmp_path / "export.csv"
    path.write_text(f"{header},other\n1,11\n2,12\n3,13\n", encoding="utf-8")
    invocation = CliRunner().invoke(app, [str(path), "--json", "--exit-code"])
    assert invocation.exit_code == 0, invocation.exception
    document = json.loads(invocation.stdout)
    assert [column["name"] for column in document["analysis"]["columns"]] == [header, "other"]


def test_literal_parquet_headers_survive_examples_ml_and_comparison(tmp_path):
    path = tmp_path / "export.parquet"
    pl.DataFrame({"^x$": [None, *range(39), 0, 1000], "*": [5] * 42}).write_parquet(path)
    invocation = CliRunner().invoke(app, [str(path), "--json", "--ml", "--baseline", str(path)])
    assert invocation.exit_code == 0, invocation.exception
    document = json.loads(invocation.stdout)
    assert not document["comparison"]["changes"]
    assert not any(metric["flagged"] for metric in document["comparison"]["drift"])
    analysis = document["analysis"]
    model = next(detector for detector in analysis["detectors"] if detector["method"] == "IsolationForest")
    assert model["columns"] == ["^x$"] and model["evaluated_rows"] == 41
    duplicate = next(finding for finding in analysis["findings"] if finding["code"] == "duplicates")
    assert set(duplicate["examples"][0]["values"]) == {"^x$", "*"}


def test_mean_shift_skips_constant_decimal_baseline():
    previous = pl.DataFrame({"amount": [0.1] * 100})
    current = pl.DataFrame({"amount": [0.1] * 99 + [1.0]})
    metrics = {metric.method: metric for metric in detect_drift(current, previous)}
    assert metrics["ks"].score == pytest.approx(0.01)
    assert not metrics["ks"].flagged
    assert metrics["mean_shift"].score is None and not metrics["mean_shift"].flagged


def test_identical_nan_code_columns_do_not_report_categorical_drift():
    frame = pl.DataFrame({"status": [float("nan")] * 100})
    metrics = {metric.method: metric for metric in detect_drift(frame, frame)}
    assert metrics["missing_rate"].score == 0
    assert not metrics["total_variation"].flagged


def test_iqr_does_not_call_distinct_large_integers_constant():
    values = [2**63 - 21 + index for index in range(9)] + [2**63 - 1]
    result = numeric_outliers(pl.Series("amount", values))
    assert result.reason is None or "precisión" in result.reason
    if result.reason is None:
        assert result.count == 1 and [example.row for example in result.examples] == [10]


def test_iqr_value_equal_to_exact_fence_is_not_an_outlier():
    values = [-13, 2, 6, 14, 20, 23, 26, 34, 37, 68]
    result = numeric_outliers(pl.Series("amount", values))
    assert result.evaluated_rows == 10
    assert float(dict(result.parameters)["superior"]) == 68
    assert result.count == 0 and not result.examples


def test_wrapped_integer_index_is_not_excluded_from_ml():
    values = [*range(128), -128, -127, -126]
    frame = pl.Series("index", values, dtype=pl.Int8).to_frame()
    result = isolation_forest(frame, enabled=True)
    assert result.columns == ("index",)
    assert result.evaluated_rows == len(values)


def test_help_does_not_show_a_value_for_json_flag():
    invocation = CliRunner().invoke(app, ["--help"], env={"COLUMNS": "100"})
    assert invocation.exit_code == 0
    assert "--json" in invocation.stdout
    assert not re.search(r"--json\s+<bool>", invocation.stdout)


def test_zero_terminal_width_still_produces_a_text_report(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("amount\n1\n2\n", encoding="utf-8")
    invocation = CliRunner().invoke(app, [str(path)], env={"COLUMNS": "0"})
    assert invocation.exit_code == 0
    assert "2 filas" in invocation.stdout
