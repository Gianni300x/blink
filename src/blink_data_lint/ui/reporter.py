"""Presentación Rich a partir de resultados; sin cálculos de calidad."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from blink_data_lint.core.models import AnalysisReport, ComparisonReport
from blink_data_lint.core.summary import summarize


def show_examples(examples, output):
    for example in examples:
        line = Text(f"  Registro {example.row:,}: ", style="dim")
        line.append(" · ".join(f"{name}={value}" for name, value in example.values.items()))
        output.print(line)


def _percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "n/d"


def _quantity(count, noun):
    return f"{count:,} {noun}{'s' if count != 1 else ''}"


def show_report(
    report: AnalysisReport, path: Path, *, format: str, policy: str,
    console: Console | None = None, comparison: ComparisonReport | None = None,
    baseline: Path | None = None,
) -> None:
    output = console if console is not None else Console()
    totals = summarize(report, comparison)
    warnings = sum(item.severity == "warning" for item in report.findings)
    if totals.warnings:
        status = "REVISAR"
    elif comparison is not None:
        status = "SIN AVISOS EN REGLAS EJECUTADAS"
    else:
        status = "SIN ALERTAS BÁSICAS"
    summary = Text()
    summary.append(str(path), style="bold")
    summary.append(f"\n{format} · {_quantity(report.rows, 'fila')} · {_quantity(len(report.columns), 'columna')}")
    summary.append(f"\nAvisos: {totals.warnings:,}", style="bold")
    if comparison is not None:
        summary.append(f" · archivo actual {warnings:,} · comparación {totals.warnings - warnings:,}")
    summary.append(f"\nInformación: {totals.information:,} · análisis omitidos/desactivados: {totals.skipped_analyses:,}")
    summary.append(f"\nNulos: {report.null_count:,} ({_percent(report.null_percent)})")
    summary.append(f" · NaN: {report.nan_count:,}")
    summary.append(f"\nDuplicados excedentes: {report.duplicate_count:,} ({_percent(report.duplicate_percent)})")
    summary.append(f"\nSalud básica: {report.coverage}\n{policy}", style="dim")
    output.print(Panel(summary, title=f"blink · {status}", border_style="yellow" if totals.warnings else "cyan"))

    if totals.warnings:
        output.print(Text("Los avisos cuentan reglas o métricas; pueden coincidir en las mismas filas.", style="dim"))
    warning_items = [f for f in report.findings if f.severity == "warning"]
    if warning_items:
        _show_findings(warning_items, "Qué revisar en el archivo", output)
    elif comparison is None or not totals.warnings:
        output.print(Text("Sin avisos en las reglas ejecutadas; revisá también la cobertura y las omisiones."))
    if comparison is not None:
        show_comparison(comparison, baseline, console=output, details=False)

    review_columns = {f.column for f in warning_items
                      if f.code not in {"nulls", "nan", "all_null", "constant"}}
    comparison_columns = ({c.column for c in comparison.changes}
                          | {m.column for m in comparison.drift if m.flagged}) if comparison else set()
    if report.columns:
        if output.width >= 72:
            table = Table(title="Salud básica por columna", expand=True, show_lines=False)
            for label in ("Columna", "Tipo", "Nulos", "NaN", "Únicos¹", "Estado"):
                table.add_column(label, overflow="fold")
            for column in report.columns:
                flags = _column_flags(column, review_columns, comparison_columns)
                table.add_row(
                    Text(column.name), Text(column.dtype),
                    f"{column.null_count:,} ({_percent(column.null_percent)})",
                    f"{column.nan_count:,}", f"{column.distinct_count:,}",
                    " · ".join(flags) or "Sin alertas básicas",
                )
            output.print(table)
        else:
            for column in report.columns:
                detail = Text()
                detail.append(column.name, style="bold")
                flags = _column_flags(column, review_columns, comparison_columns)
                detail.append("\n" + (" · ".join(flags) or "Sin alertas básicas"))
                detail.append(f"\n{column.dtype} · únicos¹: {column.distinct_count:,}")
                detail.append(f"\nNulos: {column.null_count:,} ({_percent(column.null_percent)}) · NaN: {column.nan_count:,}")
                output.print(Panel(detail))
        output.print(Text("¹ Únicos observados: excluyen null y NaN.", style="dim"))

    information = [f for f in report.findings if f.severity == "info"]
    if information:
        _show_findings(information, "Información para interpretar los datos", output)

    if any(f.examples for f in report.findings):
        output.print(Text("Sobre los ejemplos: registros de datos, no líneas físicas del CSV. Valores escapados y truncados a 100 caracteres; duplicados/ML muestran hasta 3 columnas.", style="dim"))

    if report.detectors:
        output.print(Text("\nDetalle de los detectores · cobertura y motivos", style="bold"))
        for result in report.detectors:
            line = Text()
            line.append(f"{result.method} · ", style="bold")
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
            if result.examples and not any(f.examples == result.examples for f in report.findings):
                show_examples(result.examples, output)

    if comparison is not None and comparison.drift:
        output.print(Text("\nDetalle de la comparación · métricas, cobertura y omisiones", style="bold"))
        _show_drift(comparison.drift, output)


def _column_flags(column, review_columns, comparison_columns):
    flags = []
    if column.all_null:
        flags.append("SOLO NULOS")
    if column.constant:
        flags.append("CONSTANTE")
    if column.possible_id:
        flags.append("POSIBLE ID")
    if column.null_count or column.nan_count:
        flags.append("FALTANTES")
    if column.name in review_columns:
        flags.append("REVISAR AVISOS")
    if column.name in comparison_columns:
        flags.append("REVISAR COMPARACIÓN")
    return flags


def _show_findings(items, title, output):
    output.print(Text(f"\n{title} · {len(items):,}", style="bold"))
    if any(f.examples for f in items):
        output.print(Text("Registros desde 1, sin cabecera. Hasta 3 ejemplos por aviso.", style="dim"))
    for item in items:
        prefix = "AVISO" if item.severity == "warning" else "INFO"
        if item.code == "isolation_outliers":
            prefix += " · ML exploratorio"
        line = Text(prefix, style="yellow" if item.severity == "warning" else "cyan")
        if item.column is not None:
            line.append(f" · {item.column}", style="bold")
        line.append(f": {item.message}")
        output.print(line)
        show_examples(item.examples, output)


def show_comparison(
    report: ComparisonReport, baseline: Path | None, *, console: Console | None = None,
    details: bool = True,
) -> None:
    output = console if console is not None else Console()
    summary = Text()
    summary.append("Archivo anterior: ", style="bold")
    summary.append(str(baseline) if baseline is not None else "referencia de comparación")
    summary.append(f"\nFilas: {report.baseline_rows:,} → {report.current_rows:,}")
    summary.append(f" · Columnas: {report.baseline_columns:,} → {report.current_columns:,}")
    summary.append("\nCompara esquema y valores agregados, sin emparejar filas.")
    warnings = bool(report.changes) or any(metric.flagged for metric in report.drift)
    executed = sum(metric.reason is None for metric in report.drift)
    omitted = len(report.drift) - executed
    summary.append(f"\nMétricas ejecutadas: {executed:,} · omitidas: {omitted:,}")
    if warnings:
        status = "REVISAR"
    elif omitted:
        status = "COMPARACIÓN PARCIAL" if executed else "COMPARACIÓN LIMITADA"
    else:
        status = "SIN CAMBIOS OBSERVADOS" if report.drift else "SIN CAMBIOS DE ESQUEMA"
    output.print(Panel(summary, title=f"Comparación · {status}",
                       border_style="yellow" if warnings else "cyan"))
    labels = {"added": "AGREGADA", "removed": "ELIMINADA", "type_changed": "CAMBIO DE TIPO"}
    for change in report.changes:
        line = Text(f"AVISO · {labels[change.kind]} · ", style="yellow")
        line.append(change.column, style="bold")
        line.append(f": {change.previous_type or 'ausente'} → {change.current_type or 'ausente'}")
        output.print(line)
    if not report.changes:
        output.print("Sin cambios de nombres o tipos de columnas.")
    if not report.drift:
        output.print("Distribuciones: sin columnas comunes para comparar.")
        return
    metrics = list(report.drift) if details else [metric for metric in report.drift if metric.flagged]
    if metrics:
        title = "Cambios de valores · exactos sobre observaciones elegibles" if details else "Qué revisar en la comparación"
        output.print(Text("\n" + title, style="bold"))
        _show_drift(metrics, output, compact=not details)
    elif not report.changes:
        output.print(Text("Sin avisos en las métricas ejecutadas; revisá las omisiones si las hay."))


def _show_drift(metrics, output, *, compact=False):
    methods = {"mean_shift": "CAMBIO DE MEDIA (σ anterior)", "ks": "DISTRIBUCIÓN KS",
               "total_variation": "DISTRIBUCIÓN DE CATEGORÍAS", "missing_rate": "FALTANTES",
               "distribution": "DISTRIBUCIÓN"}
    friendly = {
        "mean_shift": "La media cambió respecto del archivo anterior.",
        "ks": "La distribución numérica cambió respecto del archivo anterior.",
        "total_variation": "Cambió la proporción de categorías respecto del archivo anterior.",
        "missing_rate": "Cambió la proporción de faltantes respecto del archivo anterior.",
    }
    for metric in metrics:
        if compact:
            line = Text("AVISO · ", style="yellow")
            line.append(metric.column, style="bold")
            line.append(": " + friendly.get(metric.method, "Revisá los cambios respecto del archivo anterior."))
            output.print(line)
            continue
        status = "OMITIDO" if metric.reason else "AVISO" if metric.flagged else "INFO"
        line = Text(f"{status} · {methods[metric.method]} · ",
                    style="yellow" if metric.flagged else "dim" if metric.reason else "cyan")
        line.append(metric.column, style="bold")
        if metric.reason:
            line.append(f": {metric.reason}")
        else:
            factor = 100 if metric.method == "missing_rate" else 1
            unit = " pp" if metric.method == "missing_rate" else ""
            line.append(f": {metric.score * factor:.6g}{unit} · umbral ≥{metric.threshold * factor:g}{unit}")
            if metric.baseline_value is not None and metric.current_value is not None:
                unit = "%" if metric.method == "missing_rate" else ""
                line.append(f" · {metric.baseline_value * factor:.6g}{unit} → {metric.current_value * factor:.6g}{unit}")
        line.append(f"\nElegibles: anterior {metric.baseline_eligible:,}/{metric.baseline_rows:,}"
                    f" · actual {metric.current_eligible:,}/{metric.current_rows:,}")
        output.print(line)
