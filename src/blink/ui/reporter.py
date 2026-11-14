"""Presentación Rich a partir de resultados; sin cálculos de calidad."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from blink.core.models import AnalysisReport


def _percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "n/d"


def show_report(
    report: AnalysisReport, path: Path, *, format: str, policy: str,
    console: Console | None = None,
) -> None:
    output = console if console is not None else Console()
    warnings = sum(item.severity == "warning" for item in report.findings)
    status = "REVISAR" if warnings else "CON INDICIOS" if report.findings else "SIN ALERTAS BÁSICAS"
    summary = Text(str(path), style="bold")
    summary.append(f"\n{format} · {report.rows:,} filas · {len(report.columns):,} columnas")
    summary.append(f"\nNulos: {report.null_count:,} ({_percent(report.null_percent)})")
    summary.append(f" · NaN: {report.nan_count:,}")
    summary.append(f"\nDuplicados excedentes: {report.duplicate_count:,} ({_percent(report.duplicate_percent)})")
    summary.append(f"\nCobertura: {report.coverage}\n{policy}", style="dim")
    output.print(Panel(summary, title=f"blink · {status}", border_style="yellow" if warnings else "cyan"))

    if report.columns:
        if output.width >= 72:
            table = Table(title="Salud por columna", expand=True, show_lines=False)
            for label in ("Columna", "Tipo", "Nulos", "NaN", "Únicos¹", "Estado"):
                table.add_column(label, overflow="fold")
            for column in report.columns:
                flags = []
                if column.all_null:
                    flags.append("SOLO NULOS")
                if column.constant:
                    flags.append("CONSTANTE")
                if column.possible_id:
                    flags.append("POSIBLE ID")
                if column.null_count or column.nan_count:
                    flags.append("FALTANTES")
                table.add_row(
                    Text(column.name), Text(column.dtype),
                    f"{column.null_count:,} ({_percent(column.null_percent)})",
                    f"{column.nan_count:,}", f"{column.distinct_count:,}",
                    " · ".join(flags) or "Sin alertas",
                )
            output.print(table)
        else:
            for column in report.columns:
                detail = Text(column.name, style="bold")
                detail.append(f"\n{column.dtype} · únicos¹: {column.distinct_count:,}")
                detail.append(f"\nNulos: {column.null_count:,} ({_percent(column.null_percent)}) · NaN: {column.nan_count:,}")
                output.print(Panel(detail))
        output.print(Text("¹ Únicos observados: excluyen null y NaN.", style="dim"))

    if report.findings:
        output.print(Text(f"\nHallazgos: {len(report.findings)}", style="bold"))
        for item in report.findings:
            line = Text("AVISO" if item.severity == "warning" else "INFO", style="yellow" if item.severity == "warning" else "cyan")
            if item.column is not None:
                line.append(f" · {item.column}", style="bold")
            line.append(f": {item.message}")
            output.print(line)
    else:
        output.print("Sin hallazgos en las reglas ejecutadas. Revisá la cobertura y las omisiones.")

    if report.detectors:
        output.print(Text("\nDetectores · cobertura independiente de la salud básica", style="bold"))
        for result in report.detectors:
            line = Text(f"{result.method} · ", style="bold")
            line.append(", ".join(result.columns) or "dataset")
            if result.reason:
                line.append(f"\nOMITIDO: {result.reason}")
                line.append(f"\n0 evaluadas / {result.eligible_rows:,} elegibles / {result.total_rows:,} totales")
            else:
                scope = "muestra" if result.evaluated_rows < result.eligible_rows else "todas las elegibles"
                line.append(f"\n{result.count:,} indicios · {result.evaluated_rows:,} evaluadas "
                            f"/ {result.eligible_rows:,} elegibles / {result.total_rows:,} totales · {scope}")
            if result.parameters:
                line.append("\n" + " · ".join(f"{key}={value}" for key, value in result.parameters))
            output.print(line)
