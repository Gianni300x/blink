"""Ubicaciones verificables: filtros, muestreo, Unicode, duplicados y CSV multilínea."""
import json
from io import StringIO

import polars as pl
import pytest
from rich.console import Console
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.detectors import numeric_outliers, broken_type, isolation_forest
from blink_data_lint.ui.reporter import show_report


def test_outlier_location_keeps_missing_and_nonfinite_positions():
    values = [None, float('nan'), float('inf')] + list(map(float, range(20))) + [1000.]
    result = numeric_outliers(pl.Series('amount', values))
    assert result.count == 1
    assert [(e.row, e.values) for e in result.examples] == [(24, {'amount': '1000.0'})]


def test_broken_type_location_keeps_empty_and_original_whitespace():
    values = [None, '', ' '] + [str(i) for i in range(18)] + [' error ', 'N/A']
    result = broken_type(pl.Series('amount', values))
    assert result.count == 2
    assert [e.row for e in result.examples] == [22, 23]
    assert result.examples[0].values == {'amount': repr(' error ')}


def test_basic_examples_and_duplicate_excess_locations():
    frame = pl.DataFrame({'a': [None, 1., 1., 1., float('nan'), float('nan')],
                          'b': ['x', 'y', 'y', 'y', 'z', 'z']})
    for source in (frame, frame.lazy()):
        report = analyze(source)
        found = {f.code: f for f in report.findings}
        assert report.duplicate_count == 3
        assert [e.row for e in found['duplicates'].examples] == [3, 4, 6]
        assert [e.row for e in found['nulls'].examples] == [1]
        assert [e.row for e in found['nan'].examples] == [5, 6]


def test_examples_are_bounded_and_escaped_with_arbitrary_names():
    name = '[red]col\nname'
    frame = pl.DataFrame({name: ['secret\n' + 'ñ' * 200] * 100})
    report = analyze(frame)
    examples = next(f.examples for f in report.findings if f.code == 'constant')
    assert len(examples) == 3
    assert len(examples[0].values[name]) == 100
    assert '\\n' in examples[0].values[name]
    assert examples[0].values[name].endswith('…')


@pytest.mark.parametrize('width', [40, 80, 120])
def test_examples_render_as_text_without_markup(width, tmp_path):
    frame = pl.DataFrame({'[red]col': ['[bold]valor'] * 20})
    output = StringIO()
    show_report(analyze(frame), tmp_path/'data.csv', format='CSV', policy='test',
                console=Console(file=output, width=width, color_system=None))
    text = output.getvalue()
    assert 'Registro 1' in text
    assert '[bold]valor' in text
    assert '\x1b' not in text
    assert all(len(line) <= width for line in text.splitlines())


def test_csv_multiline_uses_logical_record_numbers_and_json_schema(tmp_path):
    path = tmp_path/'data.csv'
    path.write_text('a,b\n"one\ntwo",ok\n,ok\n,ok\n', encoding='utf-8')
    result = CliRunner().invoke(app, [str(path), '--json'])
    document = json.loads(result.stdout)
    assert document['schema_version'] == '1.1'
    finding = next(f for f in document['analysis']['findings'] if f['code'] == 'nulls')
    assert [e['row'] for e in finding['examples']] == [2, 3]
    duplicate = next(f for f in document['analysis']['findings'] if f['code'] == 'duplicates')
    assert duplicate['examples'][0]['row'] == 3


def test_ml_examples_refer_to_original_records_after_sampling():
    frame = pl.DataFrame({'x': [None] * 20 + [float(i % 20) for i in range(12000)] + [1e6] * 100})
    result = isolation_forest(frame, enabled=True)
    assert result == isolation_forest(frame, enabled=True)
    assert result.evaluated_rows == 10000
    assert result.eligible_rows == 12100
    assert len(result.examples) <= 3
    for example in result.examples:
        assert example.row > 20
        assert example.values['x'] == repr(frame[example.row - 1, 'x'])
