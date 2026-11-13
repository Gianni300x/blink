from pathlib import Path
import subprocess
import sys

import polars as pl
import pytest
from typer.testing import CliRunner

from blink.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "FILE" in result.output


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_analysis_is_read_only(tmp_path: Path, suffix: str) -> None:
    file = tmp_path / f"sample[dirty]{suffix}"
    frame = pl.DataFrame({"id": [1, 1, 2], "group": ["A", "A", None]})
    if suffix == ".csv":
        frame.write_csv(file)
    else:
        frame.write_parquet(file)
    original = file.read_bytes()
    result = runner.invoke(app, [str(file)])
    assert result.exit_code == 0, result.output
    assert "REVISAR" in result.output
    assert "Duplicados excedentes: 1" in result.output
    assert "Nulos: 1 (16.7%)" in result.output
    assert file.read_bytes() == original


@pytest.mark.parametrize("kind", ["missing", "directory", "unsupported", "corrupt"])
def test_invalid_input_fails_cleanly(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "data.csv"
    if kind == "directory":
        path.mkdir()
    elif kind == "unsupported":
        path = tmp_path / "data.txt"
        path.write_text("data")
    elif kind == "corrupt":
        path.write_bytes(b"a,b\n1,2,3\n")
    result = runner.invoke(app, [str(path)])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "SIN ALERTAS" not in result.output


def test_basic_cli_does_not_import_ml() -> None:
    code = "import blink.cli, sys; assert 'sklearn' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_dirty_fixture_expected_results() -> None:
    path = Path(__file__).parent / "data" / "sample_dirty.csv"
    result = runner.invoke(app, [str(path)])
    assert result.exit_code == 0, result.output
    assert "13 filas" in result.output
    assert "7 columnas" in result.output
    assert "Nulos: 3 (3.3%)" in result.output
    assert "Duplicados excedentes: 1" in result.output
    assert "CONSTANTE" in result.output
