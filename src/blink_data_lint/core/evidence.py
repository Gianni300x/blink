"""Ejemplos acotados y posiciones originales, sin cambiar los valores de entrada."""

import polars as pl

from blink_data_lint.core.models import RowExample
from blink_data_lint.core.execution import collect, auxiliary

EXAMPLES = 3
VALUE_CHARS = 100


def display(value) -> str:
    text = repr(value)
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS - 1] + "…"


def series_examples(series: pl.Series, mask: pl.Series) -> tuple[RowExample, ...]:
    indices = mask.fill_null(False).arg_true().head(EXAMPLES).to_list()
    return tuple(RowExample(i + 1, {series.name: display(series[i])}) for i in indices)


def frame_examples(frame: pl.DataFrame, indices, rows=None) -> tuple[RowExample, ...]:
    names = frame.columns[:3]
    return tuple(RowExample(int(rows[i]) if rows is not None else i + 1,
                            {name: display(frame[name][i]) for name in names})
                 for i in list(indices)[:EXAMPLES])


def finding_examples(source: pl.LazyFrame, finding, schema) -> tuple[RowExample, ...]:
    names = list(schema)
    row_name = auxiliary(names, "__blink_row")
    indexed = source.with_row_index(row_name, offset=1)
    if finding.code == "duplicates":
        selected = names[:3]
        query = indexed.filter(~pl.struct(names).is_first_distinct())
    elif finding.code in ("nulls", "all_null", "nan", "constant"):
        name = finding.column
        selected = [name]
        value = pl.col(name)
        if finding.code in ("nulls", "all_null"):
            mask = value.is_null()
        elif finding.code == "nan":
            mask = value.is_nan()
        else:
            mask = value.is_not_null()
            if schema[name].is_float():
                mask &= ~value.is_nan()
        query = indexed.filter(mask)
    else:
        return ()
    matches = collect(query.select(row_name, *selected).head(EXAMPLES))
    return tuple(RowExample(r[row_name], {n: display(r[n]) for n in selected})
                 for r in matches.iter_rows(named=True))
