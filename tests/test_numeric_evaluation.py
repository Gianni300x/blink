"""Contratos de evaluación; los datos reales externos no forman parte del repo."""
import importlib
from pathlib import Path
from zipfile import ZipFile

import polars as pl
import pytest


@pytest.fixture
def numeric(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'scripts'))
    return importlib.import_module('evaluate_numeric_cases')


def test_window_metrics_are_events_not_point_precision(numeric):
    times=[f'2024-01-01 0{i}:00:00' for i in range(6)]
    windows=[['2024-01-01 01:00:00','2024-01-01 02:00:00'],
             ['2024-01-01 04:00:00','2024-01-01 04:00:00']]
    result=numeric.window_coverage(times,windows,{0,1,2,4})
    assert result['windows'] == 2
    assert result['windows_hit'] == [0,1]
    assert result['alerts_inside_windows'] == 3
    assert result['alerts_outside_windows'] == 1
    assert result['outside_observations'] == 3
    assert 'precision' not in result and 'recall' not in result


@pytest.mark.parametrize('times,windows,flags', [
    (['2024-01-01 02:00:00','2024-01-01 01:00:00'],[],set()),
    (['2024-01-01 01:00:00']*2,[],set()),
    (['2024-01-01 01:00:00'],[],{1}),
    (['2024-01-01 01:00:00'],[['2024-01-02 01:00:00','2024-01-01 01:00:00']],set()),
    (['2024-01-01 01:00:00'],[['2024-01-02 01:00:00','2024-01-02 02:00:00']],set()),
])
def test_invalid_annotation_alignment_is_rejected(numeric,times,windows,flags):
    with pytest.raises(ValueError):
        numeric.window_coverage(times,windows,flags)


def test_air_preparation_uses_observations_and_preserves_sentinel(tmp_path,numeric,monkeypatch):
    import hashlib
    # Fixture de parser, no medición con datos reales ni evidencia de performance.
    rows=[';'.join(['Date',*numeric.AIR_COLUMNS])]
    rows += ['01/01/2020;'+';'.join(['-200']+['12,5']*7)]
    rows += ['01/01/2020;'+';'.join(['10']*8)]*9356
    rows += [';'*8]
    raw=('\n'.join(rows)+'\n').encode()
    archive=tmp_path/'air.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('AirQualityUCI.csv',raw)
    monkeypatch.setattr(numeric,'AIR_MEMBER_HASH',hashlib.sha256(raw).hexdigest())
    frame,truth,removed=numeric.air_data(archive)
    assert frame.shape == (9357,8)
    assert truth == {0} and removed == 1
    assert frame[0,numeric.AIR_COLUMNS[0]] == -200
    assert frame[0,numeric.AIR_COLUMNS[1]] == 12.5
    assert not any('(GT)' in n for n in frame.columns)
