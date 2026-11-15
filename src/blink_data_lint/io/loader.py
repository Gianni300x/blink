"""Lectura local estricta con detección conservadora y escaneo por columnas."""

import codecs
import csv
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

import polars as pl

from blink_data_lint.core.execution import collect

CSV_CACHE_BYTES = 16 * 1024 * 1024


class LoadError(Exception):
    """La entrada no puede inspeccionarse como dataset soportado."""


@dataclass
class LoadedDataset:
    scan: pl.LazyFrame
    format: str
    policy: str
    temporary: object | None = None

    @property
    def frame(self) -> pl.DataFrame:
        """Compatibilidad para consumidores que necesitan materializar los datos."""
        return collect(self.scan)

    def close(self) -> None:
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def __del__(self):
        self.close()


def _encoding(path: Path) -> str:
    with path.open("rb") as source:
        prefix = source.read(4)
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise LoadError("No se pudo leer: UTF-32 no soportado; exportá el CSV como UTF-8.")
    if prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif prefix.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        with path.open(encoding=encoding, newline="") as source:
            while chunk := source.read(1024 * 1024):
                if "\x00" in chunk:
                    raise LoadError("No se pudo leer: texto con bytes NUL o formato binario.")
        return encoding
    except UnicodeError:
        if encoding != "utf-8":
            raise
    # CP1252 también admite Latin-1 imprimible. No reemplazar bytes inválidos
    # silenciosamente; rechazar controles/bytes indefinidos y entradas binarias.
    with path.open(encoding="cp1252", newline="") as source:
        while chunk := source.read(1024 * 1024):
            if any(ord(c) < 32 and c not in "\t\r\n" for c in chunk):
                raise LoadError("No se pudo leer: codificación de texto no reconocida.")
    return "cp1252"


def _delimiter(path: Path, encoding: str) -> str:
    with path.open(encoding=encoding, newline="") as source:
        sample = source.read(128 * 1024)
    if not sample:
        raise LoadError("No se pudo leer: archivo vacío.")
    header = sample.splitlines()[0]
    candidates = [d for d in ",;\t|" if len(next(csv.reader([header], delimiter=d))) > 1]
    if len(candidates) > 1:
        raise LoadError("No se pudo leer: separador ambiguo en la cabecera; exportá con un único separador y campos entre comillas.")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Si no hay separadores fuera de comillas en la cabecera, puede ser
        # una columna legítima. Una estructura inconsistente no se reinterpreta.
        header = sample.splitlines()[0]
        if all(len(next(csv.reader([header], delimiter=d))) == 1 for d in ",;\t|"):
            return ","
        raise LoadError("No se pudo leer: separador ambiguo o filas inconsistentes.")


def load_dataset(path: Path) -> LoadedDataset:
    suffix = path.suffix.lower()
    temporary = None
    try:
        if not path.is_file():
            raise LoadError("La entrada debe ser un archivo local regular existente.")
        if suffix == ".csv":
            encoding = _encoding(path)
            separator = _delimiter(path, encoding)
            source = path
            if encoding not in ("utf-8", "utf-8-sig"):
                temporary = tempfile.TemporaryDirectory(prefix="blink-csv-")
                source = Path(temporary.name) / "input.csv"
                with path.open(encoding=encoding, newline="") as old, source.open("w", encoding="utf-8", newline="") as new:
                    shutil.copyfileobj(old, new, length=1024 * 1024)
            scan = pl.scan_csv(source, separator=separator, infer_schema_length=None,
                               try_parse_dates=False, ignore_errors=False,
                               encoding="utf8", raise_if_empty=True, glob=False,
                               low_memory=True)
            # Resolver el esquema valida toda la inferencia, sin cargar la tabla.
            scan.collect_schema()
            cached = path.stat().st_size >= CSV_CACHE_BYTES
            if cached:
                # Las consultas exactas necesitan varias pasadas. Convertir por
                # lotes una vez evita volver a interpretar todo el CSV en cada
                # columna. La copia es local, privada y conserva el esquema.
                if temporary is None:
                    temporary = tempfile.TemporaryDirectory(prefix="blink-csv-")
                cache = Path(temporary.name) / "cache.parquet"
                scan.sink_parquet(cache, engine="streaming")
                scan = pl.scan_parquet(cache, glob=False, low_memory=True)
            label = {",": "coma", ";": "punto y coma", "\t": "tabulador", "|": "barra"}[separator]
            label_encoding = encoding + (" (estimada)" if encoding == "cp1252" else "")
            policy = f"{label_encoding} · {label} · cabecera · inferencia completa · fechas como texto"
            if cached:
                policy += " · copia temporal local columnar"
            return LoadedDataset(scan, "CSV", policy, temporary)
        if suffix == ".parquet":
            scan = pl.scan_parquet(path, glob=False, low_memory=True)
            scan.collect_schema()
            return LoadedDataset(scan, "Parquet", "Esquema del archivo · lectura por columnas")
        raise LoadError("Formato no soportado. Usá un archivo .csv o .parquet.")
    except (OSError, pl.exceptions.PolarsError, UnicodeError) as exc:
        if temporary is not None:
            temporary.cleanup()
        raise LoadError(
            f"No se pudo leer el archivo {suffix or 'sin extensión'} "
            f"({type(exc).__name__}). Revisá permisos, codificación y estructura."
        ) from exc
    except BaseException:
        if temporary is not None:
            temporary.cleanup()
        raise
