"""Prioridad de lectura y estados fieles a cobertura, comparación y contrato JSON."""
from io import StringIO
import json
from pathlib import Path

import polars as pl
import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.comparison import compare
from blink_data_lint.core.summary import summarize
from blink_data_lint.io.serializer import serialize_report
from blink_data_lint.ui.reporter import show_report, show_comparison


def render(report, *, comparison=None, width=100):
    stream=StringIO()
    show_report(report, Path('[blue]actual.csv'), format='CSV', policy='Prueba',
                comparison=comparison, baseline=Path('[green]anterior.csv') if comparison else None,
                console=Console(file=stream, width=width, color_system=None))
    return stream.getvalue()


def test_actionable_findings_precede_metadata_and_information():
    frame=pl.DataFrame({'amount': [str(i) for i in range(99)]+['error'],
                        'customer_id':[f'u{i}' for i in range(100)]})
    text=render(analyze(frame))
    assert text.index('Qué revisar') < text.index("amount: 1 indicios")
    assert text.index('Registro 100') < text.index('Salud básica por columna')
    assert text.index('Salud básica por columna') < text.index('Información para interpretar')
    assert text.index('Información para interpretar') < text.index('Detalle de los detectores')
    assert 'REVISAR AVISOS' in text
    assert text.count('Registro 100') == 1


def test_regional_csv_shows_decimal_option_before_column_details(tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    path = tmp_path / "ventas regionales.csv"
    lines = ["pedido;importe", *(f"{row};{row},25" for row in range(1, 21))]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))
    runner = CliRunner()

    default = runner.invoke(app, [str(path), "--exit-code"])
    assert default.exit_code == 0
    summary = default.stdout.split("Salud básica por columna")[0]
    assert "importe" in summary and "--decimal-comma" in summary
    assert "sin separadores de miles" in " ".join(summary.split())

    localized = runner.invoke(app, [str(path), "--decimal-comma"])
    assert localized.exit_code == 0
    assert "--decimal-comma" not in localized.stdout


def test_baseline_only_warning_marks_global_header_and_appears_first():
    baseline=pl.DataFrame({'amount':range(100)})
    current=pl.DataFrame({'amount':range(50,150)})
    report=analyze(current)
    compared=compare(current,baseline)
    assert not report.findings
    text=render(report,comparison=compared)
    assert 'blink · REVISAR' in text.splitlines()[0]
    assert 'Avisos: 2 · archivo actual 0 · comparación 2' in text
    assert text.index('Qué revisar en la comparación') < text.index('Salud básica por columna')
    assert 'umbral' not in text[:text.index('Salud básica por columna')]
    assert 'REVISAR COMPARACIÓN' in text
    assert text.count('AVISO · DISTRIBUCIÓN KS') == 1
    assert text.count('AVISO · CAMBIO DE MEDIA') == 1
    assert text.index('Detalle de la comparación') > text.index('Detalle de los detectores')
    assert '[green]anterior.csv' in text


@pytest.mark.parametrize('rows,expected', [(40,'COMPARACIÓN LIMITADA'),(100,'SIN CAMBIOS OBSERVADOS')])
def test_comparison_distinguishes_omission_from_no_change(rows,expected):
    frame=pl.DataFrame({'amount':range(rows)})
    text=render(analyze(frame),comparison=compare(frame,frame))
    assert expected in text
    assert 'Avisos: 0' in text
    if rows==40:
        assert 'Métricas ejecutadas: 0 · omitidas: 3' in text
        assert 'OMITIDO' in text


def test_partial_comparison_is_not_presented_as_complete():
    frame=pl.DataFrame({'amount':[1]*100})
    text=render(analyze(frame),comparison=compare(frame,frame))
    assert 'COMPARACIÓN PARCIAL' in text
    assert 'Métricas ejecutadas: 2 · omitidas: 1' in text


@pytest.mark.parametrize('width',[40,80,120])
def test_reordered_report_is_readable_and_literal_at_multiple_widths(width):
    frame=pl.DataFrame({'[red]amount': [None]+list(range(98))+[10000]})
    text=render(analyze(frame),comparison=compare(frame,frame),width=width)
    assert 'REVISAR' in text
    assert '[red]' in text and '[blue]' in text
    assert 'Registro' in text
    assert '\x1b' not in text
    assert all(len(line)<=width for line in text.splitlines())


def test_rendering_does_not_mutate_serializable_report():
    frame=pl.DataFrame({'amount':range(100)})
    report=analyze(frame);compared=compare(frame,frame)
    summary=summarize(report,compared)
    before=serialize_report(report,compared,summary,sources={},exit_code=0)
    render(report,comparison=compared)
    assert serialize_report(report,compared,summary,sources={},exit_code=0)==before


@pytest.mark.parametrize('json_output',[False,True])
def test_cli_keeps_warning_exit_code_and_exact_global_count(tmp_path,json_output):
    current=tmp_path/'current.csv';baseline=tmp_path/'previous.csv'
    pl.DataFrame({'amount':range(50,150)}).write_csv(current)
    pl.DataFrame({'amount':range(100)}).write_csv(baseline)
    args=[str(current),'--baseline',str(baseline),'--exit-code']
    if json_output:
        args.append('--json')
    result=CliRunner().invoke(app,args)
    assert result.exit_code==1
    assert not result.stderr
    if json_output:
        report=json.loads(result.stdout)
        assert report['schema_version']=='1.1'
        assert report['summary']['warnings']==2
        assert 'Qué revisar' not in result.stdout
    else:
        text=Text.from_ansi(result.stdout).plain
        assert 'archivo actual 0 · comparación 2' in text
        assert text.index('Qué revisar en la comparación')<text.index('Salud básica por columna')
