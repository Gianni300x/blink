from pathlib import Path

import polars as pl
import pytest

from blink.core.analyzer import analyze
from blink.io.loader import LoadError, load_dataset


def test_csv_parquet_equivalence_and_read_only(tmp_path: Path) -> None:
    csv = tmp_path / "data[1].csv"
    csv.write_text('id,value,group\nu1,1,A\nu2,,A\nu1,1,A\n', encoding="utf-8")
    original = csv.read_bytes()
    loaded = load_dataset(csv)
    parquet = tmp_path / "data[1].parquet"
    loaded.frame.write_parquet(parquet)
    parquet_bytes = parquet.read_bytes()
    assert analyze(loaded.frame) == analyze(load_dataset(parquet).frame)
    assert csv.read_bytes() == original
    assert parquet.read_bytes() == parquet_bytes


def test_late_garbage_preserved_with_full_inference(tmp_path: Path) -> None:
    path = tmp_path / "late.csv"
    path.write_text("x,date\n" + "1,2025-01-01\n" * 150 + "error,N/A\n", encoding="utf-8")
    loaded = load_dataset(path)
    assert loaded.frame.schema == {"x": pl.String, "date": pl.String}
    assert loaded.frame[-1, "x"] == "error"
    assert loaded.frame[-1, "date"] == "N/A"


def test_csv_missing_empty_and_special_values(tmp_path: Path) -> None:
    path = tmp_path / "nulls.csv"
    path.write_text('a,b\n,ok\n"",ok\nN/A,ok\nnull,ok\nerror,ok\n', encoding="utf-8")
    assert load_dataset(path).frame["a"].to_list() == [None, "", "N/A", "null", "error"]


def test_header_only_csv(tmp_path: Path) -> None:
    path = tmp_path / "header.csv"
    path.write_text("a,b\n", encoding="utf-8")
    report = analyze(load_dataset(path).frame)
    assert report.rows == 0
    assert len(report.columns) == 2
    assert report.findings[0].code == "empty_dataset"


@pytest.mark.parametrize(("suffix", "content"), [
    (".csv", b""),
    (".csv", b"a,b\n1,2,3\n"),
    (".csv", b"a\n\xff\n"),
    (".parquet", b"not parquet"),
])
def test_invalid_inputs_are_contextual_errors(tmp_path: Path, suffix: str, content: bytes) -> None:
    path = tmp_path / f"bad{suffix}"
    path.write_bytes(content)
    with pytest.raises(LoadError, match="No se pudo leer"):
        load_dataset(path)
    assert path.read_bytes() == content


def test_missing_input_is_load_error(tmp_path: Path) -> None:
    with pytest.raises(LoadError):
        load_dataset(tmp_path / "missing.csv")
