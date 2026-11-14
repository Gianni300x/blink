"""Benchmark de proceso completo. Uso: uv run python scripts/benchmark.py."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import polars as pl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ml", action="store_true", help="Medir también el costo de IsolationForest.")
    parser.add_argument("--baseline", action="store_true", help="Medir comparación con amount desplazado +500.")
    args = parser.parse_args()
    # La generación de datos queda fuera del tiempo de ejecución de blink.
    count = 100_000
    with TemporaryDirectory(prefix="blink-benchmark-") as directory:
        frame = pl.DataFrame({
            "id": [f"usr_{i:06d}" for i in range(count)],
            "amount": [None if i % 100 == 0 else i % 1000 for i in range(count)],
            "category": [f"group_{i % 20}" for i in range(count)],
            "country": ["AR"] * count,
            "quantity": [i % 50 for i in range(count)],
            "active": [i % 2 == 0 for i in range(count)],
        })
        results = {}
        for suffix in ("csv", "parquet"):
            path = Path(directory) / f"data.{suffix}"
            current_frame = frame.with_columns(pl.col("amount") + 500) if args.baseline else frame
            if suffix == "csv":
                current_frame.write_csv(path)
            else:
                current_frame.write_parquet(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            baseline = Path(directory) / f"baseline.{suffix}"
            baseline_digest = None
            if args.baseline:
                getattr(frame, f"write_{suffix}")(baseline)
                baseline_digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
            timings = []
            command = [sys.executable, "-m", "blink.cli", str(path)]
            if args.ml:
                command.append("--ml")
            if args.baseline:
                command.extend(["--baseline", str(baseline)])
            for _ in range(7):
                start = perf_counter()
                result = subprocess.run(
                    command,
                    capture_output=True, env=dict(os.environ, COLUMNS="80", NO_COLOR="1"),
                )
                timings.append(perf_counter() - start)
                if result.returncode:
                    raise RuntimeError(result.stderr.decode(errors="replace"))
            # Proceso adicional fuera de los tiempos: RSS máximo del propio
            # proceso, no acumulado en el padre ni de otros proyectos.
            memory_code = (
                "import resource, runpy, sys, json; "
                "sys.argv = ['blink', *sys.argv[1:]]; "
                "\ntry: runpy.run_module('blink.cli', run_name='__main__')"
                "\nexcept SystemExit as exc:"
                "\n if exc.code not in (None, 0): raise"
                "\nprint(json.dumps({'max_rss': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), file=sys.stderr)"
            )
            memory = subprocess.run(
                [sys.executable, "-c", memory_code, *command[3:]],
                capture_output=True, check=True,
                env=dict(os.environ, COLUMNS="80", NO_COLOR="1"),
            )
            max_rss = json.loads(memory.stderr.decode().splitlines()[-1])["max_rss"]
            rss_bytes = max_rss if sys.platform == "darwin" else max_rss * 1024
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            if args.baseline:
                assert hashlib.sha256(baseline.read_bytes()).hexdigest() == baseline_digest
            results[suffix] = {
                "bytes": path.stat().st_size,
                "baseline_bytes": baseline.stat().st_size if args.baseline else None,
                "seconds": timings,
                "first_seconds": timings[0],
                "median_seconds": statistics.median(timings),
                "max_seconds": max(timings),
                "all_under_one_second": max(timings) < 1,
                "peak_rss_bytes_extra_process": rss_bytes,
            }
        print(json.dumps({
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "versions": {name: importlib.metadata.version(name) for name in ("polars", "typer", "rich", "numpy", "scikit-learn")},
            "ml_enabled": args.ml,
            "baseline_enabled": args.baseline,
            "rows": count, "columns": frame.width,
            "protocol": "7 fresh processes per format; includes startup, read, analysis and Rich output capture; OS cache not flushed; generation excluded",
            "results": results,
        }, indent=2))


if __name__ == "__main__":
    main()
