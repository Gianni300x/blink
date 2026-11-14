"""Resultados de inspección independientes de la terminal y del almacenamiento."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Literal["info", "warning"]
    message: str
    column: str | None = None


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
class ComparisonReport:
    baseline_rows: int
    current_rows: int
    baseline_columns: int
    current_columns: int
    changes: tuple[SchemaChange, ...]
