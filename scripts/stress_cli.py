"""Sesiones CLI reproducibles; fixtures sintéticas separadas de archivos reales.

Ejecutar desde la raíz con .venv/bin/python scripts/stress_cli.py.
No descarga datos ni escribe en el repositorio; la evidencia queda en /tmp.
"""

from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import polars as pl


@dataclass
class Case:
    name: str
    args: list[str]
    paths: tuple[Path, ...] = ()
    rows: int | None = None
    columns: list[str] | None = None
    error: bool = False
    json_output: bool = True
    usage: bool = False


def build_cases(root: Path) -> list[Case]:
    cases = []
    variants = [
        (["name", "amount"], [["Peña", i] for i in range(40)]),
        (["nombre con espacios", "importe"], [["Muñoz", None]] * 4),
        (["first\nname", "amount"], [["one\ntwo", 1], ["three", 2]]),
        (["name;with|separators", "amount"], [["a,b;c|d", 1]] * 3),
        (['say "hello"', "amount"], [['"quoted"', 1]] * 3),
        (["name", "amount"], [["x" * 150000, 1]]),
        (["name", "amount"], [["start", i] for i in range(15)] + [["end", "error"]]),
        (["name", "amount"], [["extreme", "1e308"], ["small", "-1e308"]] * 10),
        (["name", "amount"], [["finite", "NaN"], ["infinite", "inf"], ["empty", ""]]),
        (["name", "amount"], []),
        (["flag", "amount"], [["true", 1], ["false", 2]] * 10),
        (["single"], [["a,b;c|d"], ["one\ntwo"], ["Peña"]]),
        (["[red]name", "amount"], [["[bold]literal", 1]] * 4),
        (["date", "amount"], [["2026-01-01", i] for i in range(39)] + [["2026-02-30", 1000]]),
        (["index", "amount"], [[i, i % 10] for i in range(40)]),
    ]
    for index, (names, rows) in enumerate(variants):
        for sep_index, separator in enumerate(",;\t|"):
            for enc_index, encoding in enumerate(("utf-8", "utf-8-sig", "utf-16", "cp1252")):
                path = root / f"csv {index}-{sep_index}-{encoding}[ñ].csv"
                output = io.StringIO(newline="")
                writer = csv.writer(output, delimiter=separator, lineterminator="\r\n")
                writer.writerow(names)
                writer.writerows(rows)
                path.write_bytes(output.getvalue().encode(encoding))
                options = ["--json"]
                if (index + sep_index) % 2:
                    options.append("--no-examples")
                if enc_index % 2:
                    options.append("--exit-code")
                if index == 14:
                    options.append("--ml")
                # En tabuladores estos datos también forman columnas coherentes
                # usando ; o |. Rechazar esa ambigüedad es la política correcta.
                ambiguous = index == 3 and separator == "\t"
                cases.append(Case(path.stem, [str(path), *options], (path,), len(rows), names,
                                  error=ambiguous))
    for index in range(64):
        rows = index * 2
        frame = pl.DataFrame({"amount": pl.Series([None if i % 11 == 0 else float(i % 17) for i in range(rows)], dtype=pl.Float64),
                              "category": pl.Series(["Peña" if i % 2 else "a,b" for i in range(rows)], dtype=pl.String)})
        path = root / f"parquet-{index}.parquet"
        frame.write_parquet(path)
        args = [str(path), "--json"]
        if index % 2:
            args += ["--ml", "--exit-code"]
        if index % 3:
            args.append("--no-examples")
        paths = (path,)
        if index % 4 == 0:
            baseline = root / f"baseline-{index}.parquet"
            frame.rename({"category": "previous_category"}).write_parquet(baseline)
            args += ["--baseline", str(baseline)]
            paths += (baseline,)
        cases.append(Case(path.stem, args, paths, rows, frame.columns))
    invalid = [("empty.csv", b""), ("extra.csv", b"a,b\n1,2,3\n"),
               ("nul.csv", b"a\nx\x00y\n"), ("encoding.csv", b"a\n\x81\n"),
               ("bad.parquet", b"PAR1broken"), ("bad.txt", b"a\n1\n"),
               ("utf32.csv", "a\n1\n".encode("utf-32")),
               ("ambiguous.csv", b"a,b;c\n1,2;3\n"),
               ("quote.csv", b'a,b\n"unclosed,1\n'),
               ("directory.csv", None), ("missing.csv", None), ("baseline.csv", b"a\n1\n"),
               ("duplicate-header.csv", b"a,a\n1,2\n"),
               ("duplicate-quoted-header.csv", b'"say ""hi""","say ""hi"""\n1,2\n'),
               ("long-header.csv", b"x" * 150000 + b",amount\na,1\n")]
    for index, (name, content) in enumerate(invalid):
        path = root / name
        if name == "directory.csv":
            path.mkdir()
        if content is not None:
            path.write_bytes(content)
        for mode in range(4):
            args = [str(path)]
            if mode % 2:
                args.append("--json")
            if mode > 1:
                args += ["--no-examples", "--exit-code"]
            if name == "baseline.csv":
                args += ["--baseline", str(root / "missing-baseline.csv")]
            cases.append(Case(f"invalid-{index}-{mode}", args,
                              (path,) if content is not None else (), error=True, json_output=bool(mode % 2)))
    sample = Path("tests/data/sample_dirty.csv").resolve()
    for index, args in enumerate((["--unknown"], ["--baseline"], [str(sample), "--baseline"],
                                  [str(sample), "--json", "extra"], [str(sample), "--ml=maybe"], [])):
        for repeat in range(4):
            cases.append(Case(f"usage-{index}-{repeat}", list(args), usage=True, json_output=False))
    real = Path("/private/tmp/blink-numeric-eval-20261116/prepared_sensors.csv")
    for index in range(24):
        path = real if real.exists() and index % 6 == 0 else sample
        args = [str(path), "--json"]
        if index % 2:
            args.append("--no-examples")
        if index % 3 == 0:
            args.append("--ml")
        if index % 4:
            args.append("--exit-code")
        cases.append(Case(f"existing-file-{index}", args, (path,), 9357 if path == real else 13))
    return cases


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(case: Case, executable: str, root: Path):
    started = time.monotonic()
    result = {"name": case.name, "passed": False}
    try:
        before = {str(p): digest(p) for p in case.paths}
        with tempfile.TemporaryDirectory(dir=root, prefix="session-") as temporary:
            env = dict(os.environ, TMPDIR=temporary, POLARS_MAX_THREADS="2", OMP_NUM_THREADS="1", NO_COLOR="1")
            run = subprocess.run([executable, *case.args], capture_output=True, text=True,
                                 timeout=30, env=env)
            assert "Traceback" not in run.stdout + run.stderr, "traceback"
            if case.usage:
                assert run.returncode == 2, f"usage exit {run.returncode}"
            elif case.error:
                assert run.returncode == 2, f"error exit {run.returncode}"
                if case.json_output:
                    doc = json.loads(run.stdout)
                    assert doc["status"] == "error" and doc["analysis"] is None
            else:
                doc = json.loads(run.stdout)
                assert doc["status"] == "completed", "unexpected error: " + str(doc.get("error"))
                assert doc["analysis"]["rows"] == case.rows, "row count changed"
                if case.columns is not None:
                    assert [c["name"] for c in doc["analysis"]["columns"]] == case.columns, "column names changed"
                expected = int("--exit-code" in case.args and doc["summary"]["warnings"] > 0)
                assert run.returncode == doc["exit_code"] == expected, "exit policy"
                assert not run.stderr, "unexpected stderr"
                if "--no-examples" in case.args:
                    assert all(not item["examples"] for key in ("findings", "detectors") for item in doc["analysis"][key])
            assert before == {str(p): digest(p) for p in case.paths}, "input changed"
            assert not list(Path(temporary).iterdir()), "temporary files leaked"
        result["passed"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    root = Path(tempfile.mkdtemp(prefix="blink-stress-"))
    executable = str(Path(sys.executable).with_name("blink"))
    cases = build_cases(root)
    print(f"{len(cases)} sesiones, 4 procesos como máximo; evidencia: {root}", flush=True)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda case: run_case(case, executable, root), cases))
    report = {"sessions": len(results), "passed": sum(r["passed"] for r in results),
              "seconds": round(time.monotonic() - started, 2), "results": results}
    (root / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}), flush=True)
    for result in results:
        if not result["passed"]:
            print(json.dumps(result), flush=True)
    raise SystemExit(int(report["passed"] != report["sessions"]))


if __name__ == "__main__":
    main()
