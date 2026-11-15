"""Avance efímero por stderr; nunca escribe sobre el documento JSON."""

import os

from rich.console import Console
from rich.progress import Progress, ProgressColumn, SpinnerColumn, TextColumn, BarColumn
from rich.text import Text


class ColumnCount(ProgressColumn):
    def render(self, task):
        if task.total is None:
            return Text("")
        return Text(f"{int(task.completed)}/{int(task.total)} columnas", style="dim")


class WorkProgress:
    def __init__(self, *, console: Console | None = None):
        self.console = console if console is not None else Console(stderr=True)
        self.enabled = self.console.is_terminal and os.environ.get("TERM") != "dumb"
        self.progress = None
        self.task = None
        self.stage = None
        self.total = None

    def __enter__(self):
        if self.enabled:
            self.progress = Progress(
                SpinnerColumn(), TextColumn("{task.description}", markup=False),
                BarColumn(bar_width=22, complete_style="cyan", finished_style="green"),
                ColumnCount(), console=self.console, transient=True,
                refresh_per_second=8, redirect_stdout=False, redirect_stderr=False,
            )
            self.progress.start()
        return self

    def __call__(self, stage: str, completed: int | None = None, total: int | None = None):
        if self.progress is None:
            return
        if self.task is None or self.stage != stage or self.total != total:
            if self.task is not None:
                self.progress.remove_task(self.task)
            self.task = self.progress.add_task(stage, total=total, completed=completed or 0)
        else:
            self.progress.update(self.task, completed=completed or 0)
        self.stage, self.total = stage, total
        self.progress.refresh()

    def __exit__(self, exc_type, exc, traceback):
        if self.progress is not None:
            self.progress.stop()
