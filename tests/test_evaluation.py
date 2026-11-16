"""Contratos de evaluación; los resultados empíricos usan UCI real fuera del repo."""
import importlib
from pathlib import Path
from zipfile import ZipFile

import polars as pl
import pytest


@pytest.fixture
def evaluator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts'))
    return importlib.import_module('evaluate_alerts')


def write_data(path, labels=None):
    frame = pl.DataFrame({'amount': [0.] * 200 + [1e6],
                          'class': [1] * 200 + [2] if labels is None else labels})
    frame.write_csv(path)
    return path


def test_labels_do_not_influence_features_sampling_or_predictions(tmp_path, evaluator):
    first = write_data(tmp_path/'first.csv')
    second = write_data(tmp_path/'second.csv', [2] * 200 + [1])
    a = evaluator.evaluate(first, target='class', normal='1')
    b = evaluator.evaluate(second, target='class', normal='1')
    assert a['columns'] == b['columns'] == ('amount',)
    assert a['sample_rows_sha256'] == b['sample_rows_sha256']
    assert a['predictions_sha256'] == b['predictions_sha256']
    assert a['true_positive'] == 1 and b['false_positive'] == 1
    assert b['always_normal_accuracy'] == pytest.approx(1 / 201)
    assert b['majority_baseline_accuracy'] == pytest.approx(200 / 201)
    assert a['source_unchanged'] and b['source_unchanged']


@pytest.mark.parametrize('labels,normal', [([1] * 201, 'missing'), ([None] + [1] * 200, '1'), ([''] + ['a'] * 200, 'a')])
def test_invalid_label_protocol_is_rejected_before_model(tmp_path, evaluator, monkeypatch, labels, normal):
    path = write_data(tmp_path/'data.csv', labels)
    def unexpected(*args, **kwargs):
        pytest.fail('No ajustar el modelo con un protocolo inválido')
    monkeypatch.setattr(evaluator, 'analyze', unexpected)
    with pytest.raises(ValueError, match='etiqueta|etiquetas'):
        evaluator.evaluate(path, target='class', normal=normal)


def test_coverage_by_class_includes_excluded_rows(tmp_path, evaluator):
    path = tmp_path/'missing.csv'
    pl.DataFrame({'amount': list(range(100)) + [None] * 20,
                  'class': [1] * 50 + [2] * 70}).write_csv(path)
    report = evaluator.evaluate(path, target='class', normal='1')
    assert report['rows'] == 120
    assert report['eligible_rows'] == report['evaluated_rows'] == 100
    assert report['labels_in_file'] == {'1': 50, '2': 70}
    assert report['labels_in_sample'] == {'1': 50, '2': 50}
    assert report['excluded_or_not_sampled_by_label'] == {'1': 0, '2': 20}
    assert sum(report[k] for k in ['true_positive', 'false_positive', 'false_negative', 'true_negative']) == 100


def test_changed_input_invalidates_evaluation(tmp_path, evaluator, monkeypatch):
    path = write_data(tmp_path/'data.csv')
    calls = iter(['before', 'after'])
    monkeypatch.setattr(evaluator, '_sha256', lambda path: next(calls))
    with pytest.raises(ValueError, match='cambió'):
        evaluator.evaluate(path, target='class', normal='1')


def test_reference_metrics_and_model_fingerprint(tmp_path, evaluator):
    path = write_data(tmp_path/'data.csv')
    report = evaluator.evaluate(path, target='class', normal='1')
    assert report['precision'] == report['recall'] == 1
    assert report['false_positive_rate'] == 0
    assert report['positive_prevalence'] == pytest.approx(1 / 201)
    assert report['precision_lift_over_random'] == pytest.approx(201)
    assert len(report['model_source_sha256']) == 64
    assert {'polars', 'numpy', 'scikit-learn'} == set(report['versions'])


def test_public_data_suite_rejects_unexpected_source_hash(tmp_path, evaluator):
    suite = importlib.import_module('evaluate_public_data')
    with ZipFile(tmp_path/'shuttle.zip', 'w') as archive:
        archive.writestr('shuttle.tst', 'different input')
    with pytest.raises(ValueError, match='Fuente distinta'):
        suite.read_original(tmp_path, 'shuttle', 'shuttle.tst')


def test_label_column_name_does_not_collide_with_internal_aggregates(tmp_path, evaluator):
    path = tmp_path/'data.csv'
    pl.DataFrame({'amount': [0.] * 200 + [1e6], '__blink_count': [1] * 200 + [2]}).write_csv(path)
    report = evaluator.evaluate(path, target='__blink_count', normal='1')
    assert report['labels_in_file'] == {'1': 200, '2': 1}
    assert report['columns'] == ('amount',)
