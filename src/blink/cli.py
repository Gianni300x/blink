"""Entrada de terminal y coordinación; sin reglas de análisis."""

from pathlib import Path
from typing import Annotated

import typer
from polars.exceptions import PolarsError
from rich.console import Console
from rich.text import Text

from blink.core.analyzer import analyze
from blink.core.comparison import compare
from blink.core.summary import summarize
from blink.io.loader import LoadError, load_dataset
from blink.io.serializer import serialize_error, serialize_report
from blink.ui.reporter import show_comparison, show_report

app = typer.Typer(
    help="blink: diagnóstico rápido de calidad de datos CSV y Parquet, de solo lectura.",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.command()
def main(
    file: Annotated[
        Path,
        typer.Argument(
            metavar="FILE",
            help="Ruta de un archivo CSV o Parquet local.",
        ),
    ],
    ml: Annotated[bool, typer.Option("--ml", help="Ejecutar IsolationForest con muestra acotada y semilla fija.")] = False,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            metavar="FILE", help="Comparar columnas, tipos y distribuciones con un CSV o Parquet anterior.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emitir JSON para programas, sin tablas ni color.")] = False,
    exit_code: Annotated[bool, typer.Option("--exit-code", help="Devolver código 1 si hay avisos; útil en CI.")] = False,
) -> None:
    """Inspecciona salud, outliers IQR y posibles tipos rotos; --ml habilita IsolationForest."""
    sources = {
        "current": {"path": str(file), "format": None, "policy": None},
        "baseline": {"path": str(baseline), "format": None, "policy": None} if baseline is not None else None,
    }
    stage = "current"
    try:
        dataset = load_dataset(file)
        sources["current"].update(format=dataset.format, policy=dataset.policy)
        stage = "baseline"
        previous = load_dataset(baseline) if baseline is not None else None
        if previous is not None:
            sources["baseline"].update(format=previous.format, policy=previous.policy)
        stage = "analysis"
        report = analyze(dataset.frame, ml=ml)
        comparison = compare(dataset.frame, previous.frame) if previous is not None else None
    except LoadError as exc:
        reading = "baseline" if stage == "baseline" else "archivo actual"
        message = f"Error al leer el {reading}: {exc}"
        _show_error(message, sources=sources, code="read_error", stage=stage, json_output=json_output)
        raise typer.Exit(2) from exc
    except PolarsError as exc:
        message = (
            f"Error: no se pudo analizar este esquema ({type(exc).__name__}). "
            "Puede contener tipos anidados no soportados."
        )
        _show_error(message, sources=sources, code="analysis_error", stage=stage, json_output=json_output)
        raise typer.Exit(2) from exc
    except (ValueError, OverflowError) as exc:
        message = f"Error: no se pudo completar el análisis numérico ({type(exc).__name__})."
        _show_error(message, sources=sources, code="analysis_error", stage=stage, json_output=json_output)
        raise typer.Exit(2) from exc
    summary = summarize(report, comparison)
    result_code = 1 if exit_code and summary.warnings else 0
    if json_output:
        try:
            document = serialize_report(report, comparison, summary, sources=sources, exit_code=result_code)
        except ValueError as exc:
            _show_error("Error: el informe contiene métricas no representables en JSON.", sources=sources,
                        code="serialization_error", stage="serialization", json_output=True)
            raise typer.Exit(2) from exc
        typer.echo(document)
    else:
        show_report(report, file, format=dataset.format, policy=dataset.policy)
        if comparison is not None and baseline is not None:
            show_comparison(comparison, baseline)
    if result_code:
        raise typer.Exit(result_code)


def _show_error(message: str, *, sources: dict, code: str, stage: str, json_output: bool) -> None:
    if json_output:
        typer.echo(serialize_error(sources=sources, code=code, stage=stage, message=message))
        typer.echo(message, err=True)
    else:
        Console(stderr=True).print(Text(message, style="red"))


if __name__ == "__main__":
    app()
