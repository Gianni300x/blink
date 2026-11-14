from io import StringIO
from pathlib import Path

import polars as pl
import pytest
from rich.console import Console
from typer.testing import CliRunner

from blink.cli import app
from blink.core.comparison import compare
from blink.core.models import SchemaChange
from blink.ui.reporter import show_comparison


def test_added_removed_and_type_changes_have_direction() -> None:
    previous = pl.DataFrame({"amount": [1], "old": [True]})
    current = pl.DataFrame({"amount": ["error"], "new": [2.]})
    result = compare(current, previous)
    assert result.changes == (
        SchemaChange("amount", "type_changed", "Int64", "String"),
        SchemaChange("new", "added", None, "Float64"),
        SchemaChange("old", "removed", "Boolean", None),
    )
    assert (result.baseline_columns, result.current_columns) == (2, 2)


def test_values_row_counts_and_column_order_are_not_schema_changes() -> None:
    previous = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    current = pl.DataFrame({"b": ["different"], "a": [1000]})
    result = compare(current, previous)
    assert result.changes == ()
    assert (result.baseline_rows, result.current_rows) == (2, 1)


@pytest.mark.parametrize("current,previous,expected", [
    (pl.DataFrame(), pl.DataFrame(), ()),
    (pl.DataFrame(schema={"x": pl.Int64}), pl.DataFrame(schema={"x": pl.Int64}), ()),
    (pl.DataFrame(), pl.DataFrame(schema={"x": pl.Int64}),
     (SchemaChange("x", "removed", "Int64", None),)),
    (pl.DataFrame({"x": [None]}), pl.DataFrame({"x": [1]}),
     (SchemaChange("x", "type_changed", "Int64", "Null"),)),
    (pl.DataFrame(schema={"x": pl.Int32}), pl.DataFrame(schema={"x": pl.Int64}),
     (SchemaChange("x", "type_changed", "Int64", "Int32"),)),
    (pl.DataFrame(schema={"x": pl.List(pl.Int64)}), pl.DataFrame(schema={"x": pl.List(pl.String)}),
     (SchemaChange("x", "type_changed", "List(String)", "List(Int64)"),)),
])
def test_empty_null_and_nested_schemas(current, previous, expected) -> None:
    assert compare(current, previous).changes == expected


@pytest.mark.parametrize("current_suffix,previous_suffix", [
    ("csv", "csv"), ("parquet", "parquet"), ("csv", "parquet"), ("parquet", "csv"),
])
def test_cli_baseline_detects_changes_and_preserves_both_files(tmp_path: Path, current_suffix: str, previous_suffix: str) -> None:
    current = tmp_path / f"current[1].{current_suffix}"
    previous = tmp_path / f"baseline[1].{previous_suffix}"
    getattr(pl.DataFrame({"amount": [1, 2], "added": [True, False]}), f"write_{current_suffix}")(current)
    getattr(pl.DataFrame({"amount": ["a", "b"], "removed": [1, 2]}), f"write_{previous_suffix}")(previous)
    original = (current.read_bytes(), previous.read_bytes())
    output = CliRunner().invoke(app, [str(current), "--baseline", str(previous)])
    assert output.exit_code == 0, output.output
    for label in ("CAMBIO DE TIPO", "AGREGADA", "ELIMINADA", "Comparación", "Nulos:"):
        assert label in output.output
    assert "String → Int64" in output.output
    assert (current.read_bytes(), previous.read_bytes()) == original


def test_cli_same_file_and_ml_are_supported(tmp_path: Path) -> None:
    path = tmp_path / "same.csv"
    pl.DataFrame({"x": range(40)}).write_csv(path)
    result = CliRunner().invoke(app, [str(path), "--baseline", str(path), "--ml"])
    assert result.exit_code == 0, result.output
    assert "SIN CAMBIOS DE ESQUEMA" in result.output
    assert "IsolationForest" in result.output


def test_cli_without_baseline_keeps_single_file_output() -> None:
    result = CliRunner().invoke(app, [str(Path(__file__).parent / "data/sample_dirty.csv")])
    assert result.exit_code == 0
    assert "Comparación" not in result.output


@pytest.mark.parametrize("kind", ["missing", "directory", "unsupported", "corrupt_csv", "corrupt_parquet"])
def test_bad_baseline_fails_without_partial_report_or_cell_leak(tmp_path: Path, kind: str) -> None:
    current = tmp_path / "current.csv"
    current.write_text("amount\n1\n2\n")
    previous = tmp_path / "previous.csv"
    if kind == "directory":
        previous.mkdir()
    elif kind == "unsupported":
        previous = tmp_path / "previous.txt"
        previous.write_text("private_cell_value")
    elif kind == "corrupt_csv":
        previous.write_text("a,b\nprivate_cell_value,2,3\n")
    elif kind == "corrupt_parquet":
        previous = tmp_path / "previous.parquet"
        previous.write_bytes(b"private_cell_value")
    result = CliRunner().invoke(app, [str(current), "--baseline", str(previous)])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "private_cell_value" not in result.output
    assert "Nulos:" not in result.output
    assert "Comparación" not in result.output
    if kind.startswith("corrupt"):
        assert "baseline" in result.output


@pytest.mark.parametrize("width", [40, 80, 120])
def test_comparison_report_is_legible_and_literal(width: int) -> None:
    report = compare(pl.DataFrame({"[red]amount[/red]": ["error"]}), pl.DataFrame({"old": [1]}))
    stream = StringIO()
    show_comparison(report, Path("[blue]previous.csv"),
                    console=Console(file=stream, width=width, color_system=None))
    text = stream.getvalue()
    assert "REVISAR" in text
    assert "[red]" in text
    assert "[blue]previous.csv" in text
    assert "AGREGADA" in text and "ELIMINADA" in text
    assert "\x1b" not in text
    assert all(len(line) <= width for line in text.splitlines())


def test_help_keeps_one_command_with_optional_baseline() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--baseline" in result.output
    assert "Commands" not in result.output
