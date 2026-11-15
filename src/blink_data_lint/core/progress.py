"""Eventos de avance independientes de la terminal; sin porcentajes estimados."""

from collections.abc import Callable

ProgressCallback = Callable[[str, int | None, int | None], None]


def notify(callback: ProgressCallback | None, stage: str,
           completed: int | None = None, total: int | None = None) -> None:
    if callback is not None:
        callback(stage, completed, total)
