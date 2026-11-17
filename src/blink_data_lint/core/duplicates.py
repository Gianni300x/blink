"""Conteo exacto con prefiltrado de candidatos; los hashes no deciden igualdad."""

import polars as pl
from polars.selectors import by_name


def excess_duplicates(frame: pl.DataFrame | pl.LazyFrame) -> int:
    if isinstance(frame, pl.LazyFrame):
        from blink_data_lint.core.execution import collect
        if not frame.collect_schema():
            return 0
        from blink_data_lint.core.execution import auxiliary
        names = frame.collect_schema().names()
        # Solo hashes al colectar: no materializar todas las columnas para
        # descubrir que no hay filas candidatas. La igualdad final sigue exacta.
        hashes = collect(frame.select(pl.struct(by_name(*names)).hash(seed=0).alias("hash"))).to_series()
        indices = hashes.is_duplicated().arg_true()
        if not len(indices):
            return 0
        row_name = auxiliary(names, "__blink_row")
        candidates = frame.with_row_index(row_name).filter(
            pl.col(row_name).is_in(indices.implode())).drop(row_name)
        return len(indices) - collect(candidates.unique().select(pl.len())).item()
    if not frame.height or not frame.width:
        return 0
    # Filas iguales tienen el mismo hash. Una colisión solo agrega candidatos;
    # la comparación final se hace sobre sus valores completos con Polars.
    candidates = frame.hash_rows(seed=0).is_duplicated()
    count = candidates.sum()
    if not count:
        return 0
    if count == frame.height:
        return frame.height - frame.n_unique()
    return count - frame.filter(candidates).n_unique()
