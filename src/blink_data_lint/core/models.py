"""Resultados de inspección independientes de la terminal y del almacenamiento."""

from dataclasses import dataclass
import math
from typing import Literal


@dataclass(frozen=True)
class RowExample:
    row: int  # Registro de datos, desde 1; no línea física del archivo CSV.
    values: dict[str, str]


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Literal["info", "warning"]
    message: str
    column: str | None = None
    examples: tuple[RowExample, ...] = ()


@dataclass(frozen=True)
class ColumnMetrics:
    name: str
    dtype: str
    null_count: int
    null_percent: float | None
    nan_count: int
    distinct_count: int  # Valores observados; excluye null y NaN.
    constant: bool
    all_null: bool
    possible_id: bool


@dataclass(frozen=True)
class DetectorResult:
    method: str
    columns: tuple[str, ...]
    total_rows: int
    eligible_rows: int
    evaluated_rows: int
    count: int | None
    parameters: tuple[tuple[str, str], ...] = ()
    reason: str | None = None
    examples: tuple[RowExample, ...] = ()


@dataclass(frozen=True)
class AnalysisReport:
    rows: int
    columns: tuple[ColumnMetrics, ...]
    null_count: int
    null_percent: float | None
    nan_count: int
    duplicate_count: int
    duplicate_percent: float | None
    findings: tuple[Finding, ...]
    coverage: str = "exacta · todas las filas"
    detectors: tuple[DetectorResult, ...] = ()


@dataclass(frozen=True)
class SchemaChange:
    column: str
    kind: Literal["added", "removed", "type_changed"]
    previous_type: str | None
    current_type: str | None


@dataclass(frozen=True)
class DriftMetric:
    column: str
    method: str
    baseline_rows: int
    current_rows: int
    baseline_eligible: int
    current_eligible: int
    score: float | None
    threshold: float | None
    reason: str | None = None
    baseline_value: float | None = None
    current_value: float | None = None

    @property
    def flagged(self) -> bool:
        return (self.score is not None and self.threshold is not None
                and (self.score >= self.threshold
                     or math.isclose(self.score, self.threshold, rel_tol=0, abs_tol=1e-12)))


@dataclass(frozen=True)
class ComparisonReport:
    baseline_rows: int
    current_rows: int
    baseline_columns: int
    current_columns: int
    changes: tuple[SchemaChange, ...]
    drift: tuple[DriftMetric, ...] = ()


@dataclass(frozen=True)
class ReportSummary:
    warnings: int
    information: int
    skipped_analyses: int
