"""Equivalencia exacta con el camino en memoria y proyección de datos."""
from dataclasses import asdict
import importlib.util
from pathlib import Path

import polars as pl
import pytest

from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.comparison import compare
from blink_data_lint.core.duplicates import excess_duplicates
from blink_data_lint.io.loader import load_dataset


@pytest.mark.parametrize('ml', [False, True])
@pytest.mark.parametrize('suffix', ['csv', 'parquet'])
def test_file_scan_equivalence_with_missing_codes_and_ml(tmp_path, ml, suffix):
    frame = pl.DataFrame({'amount': [None] + list(range(98)) + [10000],
                          'VendorID': [1, 2] * 50,
                          '__blink_row': [f'u{i}' for i in range(100)],
                          '__blink_key': [str(i) for i in range(99)] + ['error']})
    path = tmp_path/f'data.{suffix}'
    getattr(frame, 'write_'+suffix)(path)
    loaded = load_dataset(path)
    assert analyze(loaded.scan, ml=ml) == analyze(loaded.frame, ml=ml)
    assert compare(loaded.scan, loaded.scan) == compare(loaded.frame, loaded.frame)


def test_large_sample_is_equal_for_memory_and_scan(tmp_path):
    frame = pl.DataFrame({'amount': [None] * 4 + [i % 100 for i in range(12000)],
                          'status': [1] * 12004})
    path = tmp_path/'data.parquet'
    frame.write_parquet(path)
    loaded = load_dataset(path)
    assert analyze(frame, ml=True) == analyze(loaded.scan, ml=True)


@pytest.mark.parametrize('values', [[1, 2, 3], [1, 1, 2, 2], [None, None, 2], [float('nan')] * 10, []])
def test_scan_duplicate_collision_verification(monkeypatch, values):
    frame = pl.DataFrame({'x': values})
    expected = excess_duplicates(frame)
    # Forzar todas las filas al mismo hash, incluso cuando son distintas.
    monkeypatch.setattr(pl.Expr, 'hash', lambda self, **kwargs: pl.int_range(0, pl.len()) * 0)
    assert excess_duplicates(frame.lazy()) == expected


def test_cli_never_materializes_loaded_frame(tmp_path, monkeypatch):
    from blink_data_lint.io.loader import LoadedDataset
    from blink_data_lint.cli import app
    from typer.testing import CliRunner
    frame = pl.DataFrame({'x': range(100)})
    path = tmp_path/'data.parquet'
    frame.write_parquet(path)
    def fail(self):
        raise AssertionError('La CLI no debe cargar la tabla completa')
    monkeypatch.setattr(LoadedDataset, 'frame', property(fail))
    result = CliRunner().invoke(app, [str(path), '--baseline', str(path), '--ml', '--json'])
    assert result.exit_code == 0, result.output


def test_evaluation_separates_labels_and_checks_real_detector_contract(tmp_path):
    # Fixture de contrato; la medición empírica se realiza aparte con UCI real.
    spec = importlib.util.spec_from_file_location('evaluation', Path(__file__).parents[1]/'scripts/evaluate_alerts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path/'labeled.csv'
    pl.DataFrame({'amount': [0.] * 200 + [1e6], 'class': [1] * 200 + [2]}).write_csv(path)
    result = module.evaluate(path, target='class', normal='1')
    assert result['columns'] == ('amount',)
    assert result['true_positive'] == 1
    assert result['false_positive'] == result['false_negative'] == 0
    assert result['true_negative'] == 200
    assert result['precision'] == result['recall'] == 1
    assert result['target_excluded_from_features']
