"""Evalúa cuatro archivos reales de UCI, con definiciones fijadas antes de medir.

Uso interno: python scripts/evaluate_public_data.py /ruta/archivos
Requiere wine.zip, thyroid.zip y shuttle.zip descargados de las URLs indicadas.
Escribe derivados y un informe local; no descarga ni modifica la CLI/modelo.
"""

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import polars as pl

from evaluate_alerts import evaluate, _sha256


# Hashes de los miembros originales, no del contenedor ZIP (sus metadatos pueden cambiar).
SOURCES = {
    "shuttle": ("https://archive.ics.uci.edu/static/public/148/statlog+shuttle.zip", {
        "shuttle.tst": "f776934a628d9b94c482cb058a76ddaa63823e0f74aec065813c9dff3b89d661"}),
    "thyroid": ("https://archive.ics.uci.edu/static/public/102/thyroid+disease.zip", {
        "ann-test.data": "c649ea19416e78c7996cfaaa2a9e281cb597d4b075aaa68c494fc3e4ee3aa30b"}),
    "wine": ("https://archive.ics.uci.edu/static/public/186/wine+quality.zip", {
        "winequality-red.csv": "4a402cf041b025d4566d954c3b9ba8635a3a8a01e039005d97d6a710278cf05e",
        "winequality-white.csv": "76c3f809815c17c07212622f776311faeb31e87610d52c26d87d6e361b169836"}),
}


def read_original(directory: Path, archive: str, member: str) -> bytes:
    with ZipFile(directory / f"{archive}.zip") as zipped:
        raw = zipped.read(member)
    if hashlib.sha256(raw).hexdigest() != SOURCES[archive][1][member]:
        raise ValueError(f"Fuente distinta de la fijada: {archive}/{member}; revisar antes de evaluar.")
    return raw


def prepare(directory: Path) -> dict:
    prepared = directory / "prepared"
    prepared.mkdir(exist_ok=True)
    entries = []
    for archive, member, count, features, normal in (
        ("shuttle", "shuttle.tst", 14500, 9, "1"),
        ("thyroid", "ann-test.data", 3428, 21, "3"),
        ("wine", "winequality-red.csv", 1599, 11, "normal"),
        ("wine", "winequality-white.csv", 4898, 11, "normal"),
    ):
        raw = read_original(directory, archive, member)
        if archive == "wine":
            original = pl.read_csv(BytesIO(raw), separator=";", infer_schema_length=None)
            # Solo se transforma el target; todas las mediciones conservan sus valores.
            frame = original.with_columns(pl.when(pl.col("quality").is_between(5, 7))
                                          .then(pl.lit("normal")).otherwise(pl.lit("extreme")).alias("quality"))
            target = "quality"
            definition = "quality <= 4 o quality >= 8; referencia de valoración extrema, no errores de datos"
            if not frame.drop(target).equals(original.drop(target)):
                raise ValueError("La preparación modificó las mediciones.")
            labels = dict(Counter(original[target].cast(pl.String).to_list()))
        else:
            tokens = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
            if not all(len(row) == features + 1 for row in tokens):
                raise ValueError(f"Estructura inesperada: {member}")
            target = "class"
            data = {f"measurement_{i+1}": [float(r[i]) if archive == "thyroid" else int(r[i]) for r in tokens]
                    for i in range(features)}
            data[target] = [int(r[-1]) for r in tokens]
            frame = pl.DataFrame(data)
            definition = f"clase != {normal}; clase mayoritaria como referencia, no errores de datos"
            labels = dict(Counter(frame[target].cast(pl.String).to_list()))
        if frame.shape != (count, features + 1):
            raise ValueError(f"Dimensiones inesperadas: {member}")
        output = prepared / (Path(member).stem + ".csv")
        frame.write_csv(output)
        entries.append(dict(name=Path(member).stem, file=str(output.resolve()), target=target,
                            normal=normal, positive_definition=definition, rows=count,
                            feature_count=features, original_labels=labels,
                            sha256=_sha256(output), source_url=SOURCES[archive][0],
                            source_member=member, source_sha256=SOURCES[archive][1][member]))
    protocol = dict(schema_version="1.0", model="Implementación actual de Blink; fuente y parámetros registrados por evaluación",
                    evaluation="Transductiva: ajusta y evalúa la misma muestra sin usar etiquetas. No es generalización train/test.",
                    label_policy="Definiciones preestablecidas; clases/valoraciones minoritarias no son etiquetas de errores de calidad.",
                    repetitions=2, datasets=entries)
    # Se guarda ANTES de invocar los modelos: no cambiar criterios según resultados.
    (directory / "protocol.json").write_text(json.dumps(protocol, indent=2, ensure_ascii=True), encoding="utf-8")
    return protocol


def run(directory: Path) -> dict:
    source_digests = {name: _sha256(directory / f"{name}.zip") for name in SOURCES}
    protocol = prepare(directory)
    results = []
    for entry in protocol["datasets"]:
        path = Path(entry["file"])
        first = evaluate(path, target=entry["target"], normal=entry["normal"])
        second = evaluate(path, target=entry["target"], normal=entry["normal"])
        if first != second:
            raise ValueError(f"Evaluación no reproducible: {entry['name']}")
        if _sha256(path) != entry["sha256"]:
            raise ValueError("La entrada cambió.")
        results.append(dict(name=entry["name"], label_semantics=entry["positive_definition"],
                            reproducible=True, evaluation=first))
    if any(_sha256(directory / f"{name}.zip") != digest for name, digest in source_digests.items()):
        raise ValueError("Un archivo original cambió durante la evaluación.")
    document = dict(protocol=protocol, results=results, source_archives_unchanged=True,
                    interpretation="Comparar cada dominio por separado. Sin promediar F1 entre definiciones distintas ni extrapolar precisión a errores de calidad.")
    (directory / "results.json").write_text(json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report = run(args.directory)
    for row in report["results"]:
        e = row["evaluation"]
        print(f"{row['name']}: {e['evaluated_rows']}/{e['rows']} filas; "
              f"TP={e['true_positive']} FP={e['false_positive']} FN={e['false_negative']} TN={e['true_negative']}; "
              f"precision={e['precision']:.4f} recall={e['recall']:.4f}; reproducible={row['reproducible']}")


if __name__ == "__main__":
    main()
