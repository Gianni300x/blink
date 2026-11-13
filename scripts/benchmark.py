"""Benchmark de proceso completo. Uso: uv run python scripts/benchmark.py."""

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
            if suffix == "csv":
                frame.write_csv(path)
            else:
                frame.write_parquet(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            timings = []
            for _ in range(7):
                start = perf_counter()
                result = subprocess.run(
                    [sys.executable, "-m", "blink.cli", str(path)],
                    capture_output=True, env=dict(os.environ, COLUMNS="80", NO_COLOR="1"),
                )
                timings.append(perf_counter() - start)
                if result.returncode:
                    raise RuntimeError(result.stderr.decode(errors="replace"))
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            results[suffix] = {
                "bytes": path.stat().st_size,
                "seconds": timings,
                "first_seconds": timings[0],
                "median_seconds": statistics.median(timings),
                "max_seconds": max(timings),
                "all_under_one_second": max(timings) < 1,
            }
        print(json.dumps({
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "versions": {name: importlib.metadata.version(name) for name in ("polars", "typer", "rich")},
            "rows": count, "columns": frame.width,
            "protocol": "7 fresh processes per format; includes startup, read, analysis and Rich output capture; OS cache not flushed; generation excluded",
            "results": results,
        }, indent=2))


if __name__ == "__main__":
    main()
