"""Lectura local estricta con detección conservadora y escaneo por columnas."""

import codecs
import csv
import io
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import shutil
import stat
import tempfile
from threading import Lock

import polars as pl

from blink_data_lint.core.execution import collect

CSV_CACHE_BYTES = 16 * 1024 * 1024
_CSV_FIELD_LIMIT_LOCK = Lock()


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


def _encoding(path: Path) -> tuple[str, bool]:
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
    plain = encoding == "utf-8"
    try:
        with path.open(encoding=encoding, newline="") as source:
            while chunk := source.read(1024 * 1024):
                if "\x00" in chunk:
                    raise LoadError("No se pudo leer: texto con bytes NUL o formato binario.")
                if plain and ('"' in chunk or ("\r" in chunk and chunk.count("\r") != chunk.count("\r\n"))):
                    plain = False
        return encoding, plain
    except UnicodeError:
        if encoding != "utf-8":
            raise
    # CP1252 también admite Latin-1 imprimible. No reemplazar bytes inválidos
    # silenciosamente; rechazar controles/bytes indefinidos y entradas binarias.
    with path.open(encoding="cp1252", newline="") as source:
        while chunk := source.read(1024 * 1024):
            if any(ord(c) < 32 and c not in "\t\r\n" for c in chunk):
                raise LoadError("No se pudo leer: codificación de texto no reconocida.")
    return "cp1252", False


def _delimiter(path: Path, encoding: str, *, separators: str = ",;\t|") -> tuple[str, list[str]]:
    with path.open(encoding=encoding, newline="") as source:
        sample = source.read(128 * 1024)
        truncated = bool(source.read(1))
    if not sample:
        raise LoadError("No se pudo leer: archivo vacío.")
    candidates = []
    multicolumn_headers = []
    for separator in separators:
        text = io.StringIO(sample, newline="")
        reader = csv.reader(text, delimiter=separator, strict=True)
        header = None
        widths = []
        try:
            header = next(reader)
            if truncated and text.tell() == len(sample) and not sample.endswith(("\r", "\n")):
                raise LoadError("No se pudo leer: cabecera mayor que la muestra de detección de 131.072 caracteres; acortá los nombres de columnas.")
            if header and len(separators) == 1:
                return separator, header
            if len(header) > 1:
                multicolumn_headers.append((separator, header))
            # Retener solo anchuras: la muestra no es evidencia para el informe.
            for row in reader:
                if row:
                    widths.append(len(row))
        except StopIteration:
            continue
        except csv.Error as exc:
            # Una muestra cortada puede terminar dentro de una celda o superar
            # el límite del parser de Python. Polars valida el archivo completo.
            if not truncated or header is None or not (
                "unexpected end of data" in str(exc) or "field larger" in str(exc)
            ):
                continue
        else:
            # La última fila de una muestra cortada tampoco está completa aunque
            # no tenga comillas. No usarla para descartar el separador correcto.
            if truncated and widths:
                widths.pop()
        if header and all(width == len(header) for width in widths):
            candidates.append((separator, header))
    multiple = [item for item in candidates if len(item[1]) > 1]
    if len(multiple) == 1:
        return multiple[0]
    if len(multiple) > 1:
        raise LoadError("No se pudo leer: separador ambiguo en la cabecera; exportá con un único separador y campos entre comillas.")
    # Una cabecera con un único separador deja que la validación completa
    # identifique el registro irregular, incluso dentro de la muestra.
    if len(multicolumn_headers) == 1:
        return multicolumn_headers[0]
    # Una sola columna puede contener comas literales en un CSV de tabuladores.
    # Elegir una lectura coherente de una columna, sin dividir sus valores.
    if candidates and not multicolumn_headers:
        return candidates[0]
    raise LoadError("No se pudo leer: separador ambiguo o filas inconsistentes.")


def _invalid_width(index: int, fields: int, width: int) -> LoadError:
    return LoadError(f"No se pudo leer: registro {index}. Campos encontrados: {fields}. Esperados: {width}.")


def _validate_plain_records(path: Path, separator: bytes, width: int) -> bool:
    """Cuenta campos sin crearlos en registros UTF-8 simples con LF o CRLF."""
    with path.open("rb") as source:
        for index, line in enumerate(source):
            if b'"' in line:
                return False
            if b"\r" in line and not (line.endswith(b"\r\n") and line.count(b"\r") == 1):
                return False
            if line in (b"\n", b"\r\n"):
                continue
            fields = line.count(separator) + 1
            if fields != width:
                raise _invalid_width(index, fields, width)
    return True


def _validate_records(path: Path, encoding: str, separator: str, width: int, size: int, *, plain: bool) -> str:
    """Valida registros lógicos y el salto que termina cada uno."""
    # _encoding ya validó todo el texto. Si aparecen comillas o un CR fuera de
    # CRLF, el parser estricto vuelve a leer desde la cabecera.
    if plain and _validate_plain_records(path, separator.encode("ascii"), width):
        return "\n"
    with path.open(encoding=encoding, newline="") as source, _CSV_FIELD_LIMIT_LOCK:
        last_line = ""
        endings = set()

        def physical_lines():
            nonlocal last_line
            for last_line in source:
                yield last_line

        # El límite de csv es global. Acotarlo al tamaño del archivo durante
        # esta lectura permite campos largos sin cambiarlo para los siguientes.
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(max(previous_limit, min(size, 2**31 - 1)))
        try:
            for index, row in enumerate(csv.reader(physical_lines(), delimiter=separator, strict=True)):
                if row and len(row) != width:
                    raise _invalid_width(index, len(row), width)
                if last_line.endswith("\n"):
                    endings.add("\n")
                elif last_line.endswith("\r"):
                    endings.add("\r")
        except csv.Error as exc:
            raise LoadError("No se pudo leer: revisá las comillas y la estructura de los registros.") from exc
        finally:
            csv.field_size_limit(previous_limit)
    if len(endings) > 1:
        raise LoadError("No se pudo leer: saltos de registro incompatibles. Reexportá con saltos LF o CRLF.")
    return next(iter(endings), "\n")


def load_dataset(path: Path, *, decimal_comma: bool = False) -> LoadedDataset:
    suffix = path.suffix.lower()
    temporary = None
    try:
        try:
            metadata = path.stat()
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise LoadError("No se encontró el archivo. Revisá el nombre y la ruta.") from exc
        if stat.S_ISDIR(metadata.st_mode):
            raise LoadError("La ruta apunta a una carpeta. Indicá un archivo .csv, .tsv o .parquet dentro de ella.")
        if not stat.S_ISREG(metadata.st_mode):
            raise LoadError("La entrada debe ser un archivo local regular.")
        if suffix in (".csv", ".tsv"):
            encoding, plain = _encoding(path)
            separators = "\t" if suffix == ".tsv" else ",;\t|"
            separator, header = _delimiter(path, encoding, separators=separators)
            counts = Counter(header)
            if len(counts) != len(header):
                positions = [index for index, name in enumerate(header, 1) if counts[name] > 1]
                listed = ", ".join(map(str, positions[:8]))
                extra = f" y {len(positions) - 8} más" if len(positions) > 8 else ""
                raise LoadError(
                    f"No se pudo leer: nombres de columnas repetidos en la cabecera "
                    f"(columnas {listed}{extra}, desde 1). Revisá si hay encabezados vacíos "
                    "y asigná un nombre único a cada columna."
                )
            eol = _validate_records(path, encoding, separator, len(header), metadata.st_size, plain=plain)
            source = path
            if encoding not in ("utf-8", "utf-8-sig"):
                temporary = tempfile.TemporaryDirectory(prefix="blink-csv-")
                source = Path(temporary.name) / "input.csv"
                with path.open(encoding=encoding, newline="") as old, source.open("w", encoding="utf-8", newline="") as new:
                    shutil.copyfileobj(old, new, length=1024 * 1024)
            scan = pl.scan_csv(source, separator=separator, infer_schema_length=None,
                               try_parse_dates=False, ignore_errors=False,
                               encoding="utf8", raise_if_empty=True, glob=False,
                               low_memory=True, new_columns=header, decimal_comma=decimal_comma, eol_char=eol)
            # Resolver el esquema valida toda la inferencia, sin cargar la tabla.
            scan.collect_schema()
            cached = metadata.st_size >= CSV_CACHE_BYTES
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
            if decimal_comma:
                policy += " · coma decimal"
            if cached:
                policy += " · copia temporal local columnar"
            return LoadedDataset(scan, suffix[1:].upper(), policy, temporary)
        if suffix == ".parquet":
            scan = pl.scan_parquet(path, glob=False, low_memory=True)
            scan.collect_schema()
            return LoadedDataset(scan, "Parquet", "Esquema del archivo · lectura por columnas")
        raise LoadError("Formato no soportado. Usá un archivo .csv, .tsv o .parquet.")
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
