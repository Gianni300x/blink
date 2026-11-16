"""Fixtures de contrato; los pares públicos reales se evalúan fuera del repo."""
import csv
import importlib
from pathlib import Path

import polars as pl
import pytest


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'scripts'))
    return importlib.import_module('evaluate_dirty_clean')


def write(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream).writerows(rows)
    return path


def test_truth_uses_explicit_aliases_and_normalization(tmp_path, evaluation):
    dirty = write(tmp_path/'dirty.csv', [['id','beer_name'], ['1','  A &amp; B '], ['2','wrong']])
    clean = write(tmp_path/'clean.csv', [['id','beer-name'], ['1','A & B'], ['2','correct']])
    header, rows, changed, strict = evaluation.ground_truth(dirty, clean, keys=['id'], aliases={'beer_name':'beer-name'})
    assert changed == {(1,1)}
    assert strict == 2
    with pytest.raises(ValueError, match='esquemas'):
        evaluation.ground_truth(dirty, clean, keys=['id'])


@pytest.mark.parametrize('rows', [[['2','b'],['1','a']], [['1','a'],['1','b']]])
def test_truth_rejects_reordered_or_duplicate_keys(tmp_path, evaluation, rows):
    dirty = write(tmp_path/'dirty.csv', [['id','x'],['1','a'],['2','b']])
    clean = write(tmp_path/'clean.csv', [['id','x'],*rows])
    with pytest.raises(ValueError, match='Claves'):
        evaluation.ground_truth(dirty, clean, keys=['id'])


def test_metrics_keep_unexamined_rows_outside_confusion_matrix(evaluation):
    result = evaluation.metrics({2}, {1,2,3}, {0,1,2})
    assert result['evaluated_rows'] == 3
    assert result['true_positive'] == 1
    assert result['false_negative'] == 1
    assert result['true_negative'] == 1
    assert result['positive_rows_outside_scope'] == 1
    assert result['recall'] == .5
    empty = evaluation.metrics(set(), {1}, set())
    assert empty['precision'] is empty['recall'] is None
    assert empty['positive_rows_outside_scope'] == 1
    with pytest.raises(ValueError, match='cobertura'):
        evaluation.metrics({5}, {1}, {0,1})


def test_clean_reference_never_enters_model_and_can_reveal_incremental_value(tmp_path, evaluation, monkeypatch):
    dirty = tmp_path/'dirty.csv'
    clean = tmp_path/'clean.csv'
    frame = pl.DataFrame({'id': range(201), 'amount': [0.] * 200 + [1e6]})
    frame.write_csv(dirty)
    frame.with_columns(pl.lit(0.).alias('amount')).write_csv(clean)
    original = evaluation.analyze
    seen=[]
    def check_input(candidate, **kwargs):
        assert candidate.equals(frame)
        seen.append(candidate.height)
        return original(candidate, **kwargs)
    monkeypatch.setattr(evaluation, 'analyze', check_input)
    result = evaluation.evaluate_pair(dirty, clean, keys=['id'])
    assert seen == [201]
    assert result['ml_columns'] == ('amount',)
    assert result['error_cells'] == result['error_rows'] == 1
    assert result['ml_added_true_rows'] == 1
    assert result['ml_added_false_rows'] == 0
    assert result['ml_on_errors_in_selected_columns']['precision'] == 1
    assert result['source_unchanged']


def test_row_level_success_does_not_prove_error_in_model_features(tmp_path, evaluation):
    dirty = tmp_path/'dirty.csv'; clean=tmp_path/'clean.csv'
    frame=pl.DataFrame({'id':range(201), 'amount':[0.] * 200 + [1e6], 'text':['wrong']*201})
    frame.write_csv(dirty)
    frame.with_columns(pl.lit('correct').alias('text')).write_csv(clean)
    result=evaluation.evaluate_pair(dirty,clean,keys=['id'])
    assert result['ml_on_any_error_row']['precision'] == 1
    assert result['ml_on_errors_in_selected_columns']['positive_rows'] == 0
    assert result['ml_on_errors_in_selected_columns']['precision'] == 0
    assert result['ml_on_errors_in_selected_columns']['recall'] is None
