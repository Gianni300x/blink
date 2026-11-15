"""Progreso real por etapas, fuera de stdout y sin animaciones en CI."""
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import polars as pl
import pytest
from rich.console import Console
from typer.testing import CliRunner

from blink_data_lint.cli import app
from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.comparison import compare
from blink_data_lint.ui.progress import WorkProgress


def test_column_events_are_monotonic_and_match_results():
    frame = pl.DataFrame({'x': range(100), 'group': ['a', 'b'] * 50})
    events = []
    record = lambda *event: events.append(event)
    report = analyze(frame.lazy(), ml=True, progress=record)
    assert report == analyze(frame.lazy(), ml=True)
    for stage in ['Resumiendo columnas', 'Analizando columnas']:
        assert [e[1] for e in events if e[0] == stage] == [0, 1, 2]
        assert all(e[2] == 2 for e in events if e[0] == stage)
    assert ('Buscando duplicados', None, None) in events
    assert ('Ejecutando ML', None, None) in events
    events.clear()
    assert compare(frame.lazy(), frame.lazy(), progress=record) == compare(frame, frame)
    assert events[0] == ('Comparando columnas', 0, 2)
    assert events[-1] == ('Comparando columnas', 2, 2)
    assert all(e[1] <= e[2] for e in events)


def test_redirected_console_and_dumb_terminal_stay_silent(monkeypatch):
    stream = StringIO()
    with WorkProgress(console=Console(file=stream, force_terminal=False)) as progress:
        progress('Leyendo archivo')
        progress('Analizando columnas', 1, 2)
    assert stream.getvalue() == ''
    monkeypatch.setenv('TERM', 'dumb')
    with WorkProgress(console=Console(file=stream, force_terminal=True)) as progress:
        progress('Leyendo archivo')
    assert stream.getvalue() == ''


def test_rich_progress_uses_actual_counts_and_cleans_up_on_error(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    stream = StringIO()
    progress = WorkProgress(console=Console(file=stream, force_terminal=True, width=80))
    with pytest.raises(ValueError):
        with progress:
            progress('[red]Leyendo archivo')
            progress('Analizando columnas', 1, 3)
            progress('Analizando columnas', 2, 3)
            progress('Ejecutando ML')
            task = progress.progress.tasks[0]
            assert task.total is None
            assert task.completed == 0
            raise ValueError('stop')
    text = stream.getvalue()
    assert '[red]Leyendo archivo' in text
    assert '1/3 columnas' in text and '2/3 columnas' in text
    assert '%' not in text
    assert progress.progress.live.is_started is False


@pytest.mark.parametrize('options', [[], ['--json'], ['--json', '--exit-code']])
def test_noninteractive_cli_does_not_animate(tmp_path, options):
    path = tmp_path/'data.csv'
    pl.DataFrame({'x': range(100)}).write_csv(path)
    result = CliRunner().invoke(app, [str(path), *options])
    assert result.exit_code == 0
    assert result.stderr == ''
    assert '\x1b' not in result.stdout
    if '--json' in options:
        assert json.loads(result.stdout)['status'] == 'completed'


@pytest.mark.skipif(sys.platform == 'win32', reason='PTY POSIX; consola simulada cubierta en Windows')
def test_real_tty_progress_is_only_on_stderr_while_json_is_redirected(tmp_path):
    import pty
    import threading
    master, slave = pty.openpty()
    chunks = []
    def drain():
        while True:
            try:
                block = os.read(master, 65536)
            except OSError:
                break
            if not block:
                break
            chunks.append(block)
    thread = threading.Thread(target=drain)
    thread.start()
    path = tmp_path/'data.parquet'
    pl.DataFrame({'amount': range(1000)}).write_parquet(path)
    output = tmp_path/'report.json'
    try:
        with output.open('wb') as stream:
            result = subprocess.run([sys.executable, '-m', 'blink_data_lint.cli', str(path), '--json', '--ml'],
                                    stdout=stream, stderr=slave, env=dict(os.environ, TERM='xterm', PYTHONIOENCODING='utf-8'), timeout=30)
        os.close(slave)
        thread.join(timeout=5)
        assert not thread.is_alive()
        text = b''.join(chunks).decode('utf-8')
        assert result.returncode == 0
        assert 'Leyendo archivo' in text and 'Analizando columnas' in text and 'Ejecutando ML' in text
        assert json.loads(output.read_text())['status'] == 'completed'
        assert '\x1b' not in output.read_text()
    finally:
        os.close(master)
