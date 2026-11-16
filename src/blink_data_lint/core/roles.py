"""Indicios conservadores por nombre; nunca afirman semántica de negocio."""

import re

CODE_TOKENS = {"id", "code", "codigo", "código", "type", "tipo", "status", "estado"}


def code_like(name: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return bool(CODE_TOKENS.intersection(re.split(r"[\W_]+", separated.casefold())))


ROW_INDEX_NAMES = {"index", "row_index", "rowindex", "row_number", "rownumber",
                   "unnamed_0", "index_level_0"}


def positional_index(series) -> bool:
    """Indicio acotado: nombre de índice y contador entero 0..n-1 / 1..n.

    Una medida creciente con otro nombre o un índice económico no consecutivo
    conserva su análisis. No convierte ni ordena valores para forzar el indicio.
    """
    name = re.sub(r"[\W_]+", "_", series.name.casefold()).strip("_")
    if name not in ROW_INDEX_NAMES or not series.dtype.is_integer() or len(series) < 2:
        return False
    if series.null_count() or series[0] not in (0, 1):
        return False
    return bool((series.diff().drop_nulls() == 1).all())
