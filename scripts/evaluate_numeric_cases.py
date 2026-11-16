"""Evaluación de señales numéricas reales: UCI Air Quality y dos series NAB.

Uso interno: python scripts/evaluate_numeric_cases.py /ruta/fuentes
Requiere air-quality.zip, combined_windows.json y los dos CSV NAB fijados abajo.
No descarga datos, inyecta errores, cambia parámetros ni calcula el score oficial NAB.
"""

import argparse
from datetime import datetime
from io import BytesIO
import json
import importlib.metadata
import sys
from pathlib import Path
from zipfile import ZipFile

import polars as pl

from evaluate_dirty_clean import digest, metrics, ml_rows, rule_rows
from blink_data_lint.core.analyzer import analyze

AIR_MEMBER_HASH = '13277ae5d8581e80b7be09d47c7d3d06fe9b8e957078f2cf6e859f955e62f996'
FILES = {
    'combined_windows.json': 'ff0d0be29f60cb2c10c9a3c6d3b6e078843f0ca8983c148c20dfe012f3dbca20',
    'ec2_cpu_utilization_24ae8d.csv': 'ab446fbd8b9f37507eb2fdb06315826d8daeef02e241133ce06e0ee571ba53d9',
    'ec2_cpu_utilization_53ea38.csv': '8942e498de7b40f1b4a6d802755592c09b8fb73ce8f658763a09cb9ea1ea94ac',
}
AIR_COLUMNS = ['PT08.S1(CO)', 'PT08.S2(NMHC)', 'PT08.S3(NOx)',
               'PT08.S4(NO2)', 'PT08.S5(O3)', 'T', 'RH', 'AH']


def air_data(archive):
    import hashlib
    with ZipFile(archive) as source:
        raw = source.read('AirQualityUCI.csv')
    if hashlib.sha256(raw).hexdigest() != AIR_MEMBER_HASH:
        raise ValueError('Fuente UCI distinta de la fijada.')
    frame = pl.read_csv(BytesIO(raw), separator=';', decimal_comma=True, infer_schema_length=None)
    blanks = frame.filter(pl.col('Date').is_null())
    if blanks.select(pl.any_horizontal(pl.all().is_not_null()).any()).item():
        raise ValueError('Hay filas sin fecha que no están vacías; no descartarlas.')
    frame = frame.filter(pl.col('Date').is_not_null()).select(AIR_COLUMNS)
    if frame.height != 9357 or frame.null_count().sum_horizontal().sum():
        raise ValueError('Dimensiones o faltantes inesperados.')
    # Positivo fijado por el publicador, no aprendido de etiquetas de clase.
    # Los -200 se conservan como números: así las reglas y ML pueden examinarlos.
    truth = set(frame.select(pl.any_horizontal(pl.all() == -200)).to_series().arg_true().to_list())
    return frame, truth, blanks.height


def window_coverage(timestamps, windows, predicted):
    times = [datetime.fromisoformat(t) for t in timestamps]
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError('Timestamps desordenados o duplicados.')
    if not set(predicted) <= set(range(len(times))):
        raise ValueError('Predicciones fuera del archivo.')
    spans = [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a,b in windows]
    if any(a>b for a,b in spans) or spans != sorted(spans) or any(b>=c for (_,b),(c,_) in zip(spans,spans[1:])):
        raise ValueError('Ventanas inválidas o superpuestas.')
    inside = set()
    hits = []
    for n,(start,end) in enumerate(spans):
        indices = {i for i,t in enumerate(times) if start<=t<=end}
        if not indices:
            raise ValueError('Ventana sin observaciones.')
        inside |= indices
        if indices & predicted:
            hits.append(n)
    outside = set(range(len(times))) - inside
    return dict(windows=len(windows), windows_hit=hits, alerts=len(predicted),
                alerts_inside_windows=len(predicted&inside), alerts_outside_windows=len(predicted&outside),
                outside_observations=len(outside),
                outside_alerts_per_1000=len(predicted&outside)*1000/len(outside) if outside else None)


def evaluate(directory, protocol):
    air, truth, blanks = air_data(directory/'air-quality.zip')
    air.write_csv(directory/'prepared_sensors.csv')
    if not pl.read_csv(directory/'prepared_sensors.csv', infer_schema_length=None).equals(air):
        raise ValueError('El CSV preparado altera los valores.')
    report = analyze(air, ml=True)
    ml, scope = ml_rows(air, report.detectors[-1])
    rules, checks = rule_rows(air, report)
    if len(scope) != air.height:
        raise ValueError('ML no evaluó todos los registros numéricos.')
    air_result = dict(rows=air.height, empty_lines_removed=blanks, columns=AIR_COLUMNS,
                      positive_definition='Al menos una medición == -200: faltante documentado por UCI.',
                      limitation='Los no-sentinela no se certifican libres de otros errores. No se mide drift ni discrepancia respecto del analizador.',
                      reference_columns_excluded=True, target_in_input=False,
                      ml=metrics(ml,truth,scope), rules=metrics(rules,truth,range(air.height)),
                      combined=metrics(ml|rules,truth,range(air.height)),
                      added_true_rows=len((ml-rules)&truth), added_non_sentinel_rows=len((ml-rules)-truth),
                      ml_parameters=dict(report.detectors[-1].parameters), rule_checks=checks)
    labels = json.loads((directory/'combined_windows.json').read_text())
    nab = {}
    for file in protocol['nab_files']:
        frame = pl.read_csv(directory/file, infer_schema_length=None)
        if frame.columns != ['timestamp','value'] or frame.height>10000 or frame.null_count().sum_horizontal().sum():
            raise ValueError('Esquema/tamaño NAB inesperado.')
        windows = labels['realAWSCloudwatch/'+file]
        result = analyze(frame, ml=True)
        found, scope = ml_rows(frame,result.detectors[-1])
        base, checks = rule_rows(frame,result)
        if len(scope) != frame.height:
            raise ValueError('ML no cubre toda la serie NAB.')
        m = window_coverage(frame['timestamp'].to_list(),windows,found)
        r = window_coverage(frame['timestamp'].to_list(),windows,base)
        nab[file] = dict(rows=frame.height, ml=m, rules=r,
                         combined=window_coverage(frame['timestamp'].to_list(),windows,found|base),
                         added_windows=sorted(set(m['windows_hit'])-set(r['windows_hit'])),
                         ml_parameters=dict(result.detectors[-1].parameters), rule_checks=checks,
                         interpretation='Cobertura de ventanas etiquetadas, no errores de celdas ni precisión puntual. Diagnóstico offline/transductivo, no score oficial/online NAB.')
    return dict(air_quality=air_result,nab=nab)


def run(directory):
    for name,sha in FILES.items():
        if digest(directory/name)!=sha:
            raise ValueError(f'Fuente distinta: {name}')
    before = {p.name:digest(p) for p in [directory/'air-quality.zip',*[directory/n for n in FILES]]}
    protocol = dict(model='Blink actual; sin modificar parámetros ni optimizar con resultados',
                    air_definition='== -200 en cualquiera de las 8 mediciones físicas; no usar referencias GT como inputs',
                    nab_files=sorted(n for n in FILES if n.endswith('.csv')),
                    selection='Primeros dos CSV ec2_cpu_utilization por orden lexicográfico, fijados antes de medir',
                    nab_definition='Ventanas originales inclusivas; cobertura de eventos y avisos fuera de ventanas. Sin F1 puntual ni score NAB.',
                    source_sha256=before)
    (directory/'numeric_protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    source = Path(__file__).resolve().parents[1] / "src" / "blink_data_lint"
    protocol["implementation_sha256"] = {str(p.relative_to(source)):digest(p) for p in source.rglob("*.py")}
    protocol["python"] = sys.version.split()[0]
    protocol["versions"] = {n:importlib.metadata.version(n) for n in ("polars","numpy","scikit-learn")}
    (directory/'numeric_protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    first = evaluate(directory,protocol)
    second = evaluate(directory,protocol)
    if first!=second or any(digest(directory/n)!=sha for n,sha in before.items()):
        raise ValueError('Resultado no reproducible o fuentes modificadas.')
    document = dict(protocol=protocol,reproducible=True,sources_unchanged=True,results=first)
    (directory/'numeric_results.json').write_text(json.dumps(document,indent=2,allow_nan=False),encoding='utf-8')
    return document


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    result=run(args.directory)
    print(json.dumps(result['results'],indent=2,allow_nan=False))


if __name__=='__main__':
    main()
