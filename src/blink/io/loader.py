"""Lectura local y estricta; no corrige ni escribe los datos de entrada."""

from dataclasses import dataclass
from pathlib import Path

import polars as pl


class LoadError(Exception):
    """La entrada no puede inspeccionarse como dataset soportado."""


@dataclass(frozen=True)
class LoadedDataset:
    frame: pl.DataFrame
    format: str
    policy: str


def load_dataset(path: Path) -> LoadedDataset:
    suffix = path.suffix.lower()
    try:
        if not path.is_file():
            raise LoadError("La entrada debe ser un archivo local regular existente.")
        # Un handle evita interpretar caracteres como '[' o '*' como un glob.
        with path.open("rb") as source:
            if suffix == ".csv":
                frame = pl.read_csv(
                    source,
                    infer_schema_length=None,
                    try_parse_dates=False,
                    ignore_errors=False,
                    encoding="utf8",
                    raise_if_empty=True,
                )
                return LoadedDataset(
                    frame,
                    "CSV",
                    "UTF-8 · coma · cabecera · inferencia completa · fechas como texto",
                )
            if suffix == ".parquet":
                return LoadedDataset(pl.read_parquet(source), "Parquet", "Esquema del archivo")
            raise LoadError("Formato no soportado. Usá un archivo .csv o .parquet.")
    except (OSError, pl.exceptions.PolarsError, UnicodeError) as exc:
        # No mostrar valores de celdas que algunos errores de Polars incluyen.
        raise LoadError(
            f"No se pudo leer el archivo {suffix or 'sin extensión'} "
            f"({type(exc).__name__}). Revisá permisos, codificación y estructura."
        ) from exc
