"""Consultas por lotes y nombres auxiliares sin colisiones con datos del usuario."""

import polars as pl


def collect(query: pl.LazyFrame) -> pl.DataFrame:
    return query.collect(engine="streaming")


def auxiliary(names, stem: str) -> str:
    name = stem
    while name in names:
        name += "_"
    return name
