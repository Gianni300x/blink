from pathlib import Path
import csv
import io

import polars as pl
import pytest

from blink_data_lint.core.analyzer import analyze
from blink_data_lint.io.loader import LoadError, load_dataset


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
    (".csv", b"a\n\x81\n"),
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


@pytest.mark.parametrize('separator', [',', ';', '\t', '|'])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'cp1252', 'utf-16'])
def test_excel_csv_dialects_and_encodings_are_visible(tmp_path, separator, encoding):
    path = tmp_path / 'datos[1].csv'
    text = f'nombre{separator}valor\r\n"Muñoz, José"{separator}12\r\n"Peña; Ana"{separator}15\r\n'
    path.write_bytes(text.encode(encoding))
    original = path.read_bytes()
    loaded = load_dataset(path)
    temporary = Path(loaded.temporary.name) if loaded.temporary else None
    try:
        assert loaded.frame['nombre'].to_list() == ['Muñoz, José', 'Peña; Ana']
        assert loaded.frame['valor'].to_list() == [12, 15]
        assert encoding in loaded.policy
        assert path.read_bytes() == original
    finally:
        loaded.close()
    if temporary:
        assert not temporary.exists()


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_csv_quoted_separator_and_multiline(tmp_path, line_ending):
    path = tmp_path/'quoted.csv'
    text = 'name;amount\n"one;two\nthree";10\n"four,five";20\n'.replace('\n', line_ending)
    path.write_bytes(text.encode('utf-8'))
    loaded = load_dataset(path)
    assert loaded.frame.shape == (2, 2)
    assert loaded.frame[0, 'name'] == f'one;two{line_ending}three'
    assert 'punto y coma' in loaded.policy


def test_late_undefined_encoding_is_not_lossily_replaced(tmp_path):
    path = tmp_path/'late.csv'
    path.write_bytes(b'name\n' + b'valid\n' * 200000 + b'\x81\n')
    with pytest.raises(LoadError):
        load_dataset(path)


def test_transcoded_csv_temp_is_cleaned_when_baseline_fails(tmp_path, monkeypatch):
    import blink_data_lint.io.loader as loader
    from blink_data_lint.cli import app
    from typer.testing import CliRunner
    real = loader.tempfile.TemporaryDirectory
    directories = []
    def track(*args, **kwargs):
        directory = real(*args, **kwargs)
        directories.append(Path(directory.name))
        return directory
    monkeypatch.setattr(loader.tempfile, 'TemporaryDirectory', track)
    path = tmp_path/'data.csv'
    path.write_bytes('name;value\nJosé;1\n'.encode('cp1252'))
    result = CliRunner().invoke(app, [str(path), '--baseline', str(tmp_path/'missing.csv'), '--json'])
    assert result.exit_code == 2
    assert directories and all(not p.exists() for p in directories)


@pytest.mark.parametrize('content', [b'a,b;c\n1,2;3\n', b'a\nhello\x00world\n', 'a\nhello\n'.encode('utf-32')])
def test_ambiguous_or_binary_csv_is_rejected(tmp_path, content):
    path = tmp_path/'ambiguous.csv'
    path.write_bytes(content)
    with pytest.raises(LoadError, match='No se pudo leer'):
        load_dataset(path)


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_columnar_cache_preserves_csv_values_and_record_order(tmp_path, monkeypatch, line_ending):
    import blink_data_lint.io.loader as loader
    monkeypatch.setattr(loader, 'CSV_CACHE_BYTES', 1)
    path = tmp_path/'data.csv'
    text = 'name;value\n"one\ntwo";1\n"";\n;3\n"Muñoz";error\n'.replace('\n', line_ending)
    path.write_bytes(text.encode('utf-8'))
    original = path.read_bytes()
    loaded = load_dataset(path)
    temporary = Path(loaded.temporary.name)
    try:
        assert 'copia temporal local columnar' in loaded.policy
        assert loaded.frame['name'].to_list() == [f'one{line_ending}two', '', None, 'Muñoz']
        assert loaded.frame['value'].to_list() == ['1', None, '3', 'error']
        assert (temporary/'cache.parquet').exists()
        assert path.read_bytes() == original
    finally:
        loaded.close()
    assert not temporary.exists()


def test_cache_disk_failure_is_sanitized_and_cleaned(tmp_path, monkeypatch):
    import blink_data_lint.io.loader as loader
    from blink_data_lint.cli import app
    from typer.testing import CliRunner
    monkeypatch.setattr(loader, 'CSV_CACHE_BYTES', 1)
    real = loader.tempfile.TemporaryDirectory
    directories = []
    def track(*args, **kwargs):
        directory = real(*args, **kwargs)
        directories.append(Path(directory.name))
        return directory
    def fail(*args, **kwargs):
        raise OSError('PRIVATE_VALUE')
    monkeypatch.setattr(loader.tempfile, 'TemporaryDirectory', track)
    monkeypatch.setattr(pl.LazyFrame, 'sink_parquet', fail)
    path = tmp_path/'data.csv'
    path.write_text('amount\n1\n2\n')
    result = CliRunner().invoke(app, [str(path), '--json'])
    assert result.exit_code == 2
    assert 'PRIVATE_VALUE' not in result.stdout + result.stderr
    assert directories and all(not p.exists() for p in directories)


@pytest.mark.parametrize("separator", [",", ";", "\t", "|"])
@pytest.mark.parametrize("kind", ["separators_in_header", "escaped_header", "long_cell", "single_column"])
def test_csv_stress_regressions_preserve_names_values_and_rows(tmp_path, separator, kind):
    names = ["name", "amount"]
    rows = [["Peña", "1"], ["Muñoz", "2"]]
    if kind == "separators_in_header":
        names = ["name;with|separators", "amount"]
        rows = [["a,b;c|d", "1"], ["one\ntwo", "2"]]
    elif kind == "escaped_header":
        names = ['say "hello"', "amount"]
        rows = [['"quoted"', "1"], ["other", "2"]]
    elif kind == "long_cell":
        rows = [["x" * 150000, "1"], ["tail", "2"]]
    else:
        names = ["single"]
        rows = [["a,b;c|d"], ["one\ntwo"], ["Peña"]]
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=separator)
    writer.writerow(names)
    writer.writerows(rows)
    path = tmp_path / "data.csv"
    path.write_bytes(output.getvalue().encode("utf-8"))
    before = path.read_bytes()
    loaded = load_dataset(path)
    try:
        frame = loaded.frame
        assert frame.columns == names
        assert frame.height == len(rows)
        assert frame[names[0]].to_list() == [row[0] for row in rows]
    finally:
        loaded.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", [
    'a,b\n1,2,3\n', 'a;b\n1;2;3\n',
    'a,b;c\n1,2;3\n', 'a,b\n"unterminated,1\n',
])
def test_dialect_detection_does_not_hide_corruption_as_one_column(tmp_path, content):
    path = tmp_path / "invalid.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(LoadError):
        load_dataset(path)


def test_multiple_consistent_separators_remain_ambiguous(tmp_path):
    path = tmp_path / "ambiguous.csv"
    path.write_text("name;with|separators\tamount\na,b;c|d\t1\na,b;c|d\t2\n", encoding="utf-8")
    with pytest.raises(LoadError, match="ambiguo"):
        load_dataset(path)


def test_header_beyond_detection_sample_is_not_silently_truncated(tmp_path):
    path = tmp_path / "header.csv"
    path.write_text("x" * 150000 + ",amount\na,1\n", encoding="utf-8")
    with pytest.raises(LoadError, match="cabecera mayor"):
        load_dataset(path)


@pytest.mark.parametrize("header", ["a,a", '"say ""hi""","say ""hi"""'])
def test_duplicate_header_names_have_a_clear_error(tmp_path, header):
    from blink_data_lint.cli import app
    from typer.testing import CliRunner
    import json

    path = tmp_path / "duplicates.csv"
    path.write_text(header + "\n1,2\n", encoding="utf-8")
    with pytest.raises(LoadError, match="nombres de columnas repetidos"):
        load_dataset(path)
    result = CliRunner().invoke(app, [str(path), "--json"])
    assert result.exit_code == 2
    document = json.loads(result.stdout)
    assert document["status"] == "error" and document["analysis"] is None
    assert "nombres de columnas repetidos" in document["error"]["message"]


def test_repeated_empty_headers_report_positions(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("fecha;importe;;\n2026-11-17;2;;\n", encoding="utf-8")
    with pytest.raises(LoadError, match=r"columnas 3, 4, desde 1.*encabezados vacíos"):
        load_dataset(path)
