"""Consultas por lotes y nombres auxiliares sin colisiones con datos del usuario."""

import polars as pl


def collect(query: pl.LazyFrame) -> pl.DataFrame:
    return query.collect(engine="streaming")


def count_rows(frame: pl.DataFrame | pl.LazyFrame) -> int:
    return collect(frame.select(pl.len())).item() if isinstance(frame, pl.LazyFrame) else frame.height


def auxiliary(names, stem: str) -> str:
    name = stem
    while name in names:
        name += "_"
    return name
