from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from blink_data_lint.core.models import AnalysisReport, ColumnMetrics, Finding
from blink_data_lint.ui.reporter import show_report


@pytest.mark.parametrize("width", [40, 80, 120])
def test_report_is_legible_without_color(width: int) -> None:
    stream = StringIO()
    report = AnalysisReport(
        rows=2,
        columns=(
            ColumnMetrics("[red]id[/red]", "String", 0, 0.0, 0, 1, True, False, False),
            ColumnMetrics("missing", "Null", 2, 100.0, 0, 0, False, True, False),
        ),
        null_count=2, null_percent=50.0, nan_count=0,
        duplicate_count=1, duplicate_percent=50.0,
        findings=(Finding("all_null", "warning", "2 nulos (100.0%).", "missing"),),
    )
    show_report(report, Path("[blue]data.csv"), format="CSV", policy="Prueba", console=Console(
        file=stream, width=width, color_system=None, force_terminal=False,
    ))
    text = stream.getvalue()
    assert "REVISAR" in text
    # Rich puede plegar nombres largos en una tabla estrecha.
    assert "[red]" in text and "[/red]" in text
    assert "[blue]data.csv" in text
    assert "AVISO" in text
    assert "\x1b" not in text
    assert all(len(line) <= width for line in text.splitlines())


def test_empty_report_is_not_shown_as_healthy() -> None:
    stream = StringIO()
    report = AnalysisReport(
        0, (), 0, None, 0, 0, None,
        (Finding("empty_dataset", "warning", "Dataset sin datos para evaluar."),),
    )
    show_report(report, Path("empty.parquet"), format="Parquet", policy="Prueba", console=Console(file=stream))
    assert "REVISAR" in stream.getvalue()
    assert "n/d" in stream.getvalue()


def test_healthy_data_has_no_basic_alerts() -> None:
    stream = StringIO()
    report = AnalysisReport(
        3, (ColumnMetrics("number", "Int64", 0, 0.0, 0, 3, False, False, False),),
        0, 0.0, 0, 0, 0.0, (),
    )
    show_report(report, Path("good.csv"), format="CSV", policy="Prueba", console=Console(file=stream))
    assert "SIN ALERTAS BÁSICAS" in stream.getvalue()
