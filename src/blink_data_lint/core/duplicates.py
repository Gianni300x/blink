"""Conteo exacto con prefiltrado de candidatos; los hashes no deciden igualdad."""

import polars as pl


def excess_duplicates(frame: pl.DataFrame) -> int:
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
