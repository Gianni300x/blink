"""Entrada de terminal y coordinación; sin reglas de análisis."""

from pathlib import Path
from typing import Annotated

import typer
from polars.exceptions import PolarsError
from rich.console import Console
from rich.text import Text

from blink.core.analyzer import analyze
from blink.io.loader import LoadError, load_dataset
from blink.ui.reporter import show_report

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
            exists=True, file_okay=True, dir_okay=False, readable=True,
            metavar="FILE",
            help="Ruta de un archivo CSV o Parquet local.",
        ),
    ],
    ml: Annotated[bool, typer.Option("--ml", help="Ejecutar IsolationForest con muestra acotada y semilla fija.")] = False,
) -> None:
    """Inspecciona salud, outliers IQR y posibles tipos rotos; --ml habilita IsolationForest."""
    if file.suffix.lower() not in {".csv", ".parquet"}:
        raise typer.BadParameter(
            "Formato no soportado. Usá un archivo .csv o .parquet.", param_hint="FILE",
        )
    try:
        dataset = load_dataset(file)
        report = analyze(dataset.frame, ml=ml)
    except LoadError as exc:
        Console(stderr=True).print(Text(f"Error: {exc}", style="red"))
        raise typer.Exit(2) from exc
    except PolarsError as exc:
        Console(stderr=True).print(Text(
            f"Error: no se pudo analizar este esquema ({type(exc).__name__}). "
            "Puede contener tipos anidados no soportados.", style="red",
        ))
        raise typer.Exit(2) from exc
    show_report(report, file, format=dataset.format, policy=dataset.policy)


if __name__ == "__main__":
    app()
