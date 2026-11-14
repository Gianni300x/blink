# Benchmark del Hito 1

Estado: **ejecutado y aprobado para este fixture** (2026-11-13).
Las siete ejecuciones por formato terminaron en menos de un segundo. Las
36 pruebas funcionales también pasaron. La instalación inicial fue desbloqueada
por el usuario desde su terminal; las mediciones usan el entorno `.venv` instalado.

## Protocolo reproducible

Desde la raíz del repositorio, después de instalar dependencias:

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/benchmark.py
```

El script genera 100.000 filas y seis columnas (texto, enteros y booleanos), con
1 % de nulos en `amount`, una columna constante y un ID de texto único. Guarda
CSV y Parquet en un directorio temporal y ejecuta siete procesos nuevos por formato.
La generación y escritura de los fixtures no se incluyen en los tiempos.

Se mide desde el lanzamiento del proceso hasta su finalización, incluyendo imports,
lectura, análisis exacto y renderizado Rich capturado, sin color y con ancho 80.
Se informa cada tiempo, primera ejecución, mediana y máximo, tamaño de archivo,
versiones de Python/Polars/Typer/Rich y datos del sistema. No se vacían cachés del
sistema operativo: el primer proceso tampoco debe llamarse una medición de disco frío.
El digest de cada archivo se comprueba tras las siete ejecuciones.

El criterio para este fixture es que las siete ejecuciones de cada formato
terminen en <1 s. Se cumplió en esta máquina; no garantiza ese rendimiento para
otros datasets, dispositivos ni condiciones de almacenamiento.

## Resultados

Entorno: Python 3.12.13, macOS-27.2.1-arm64-arm-64bit, 6 CPU lógicas;
Polars 1.44.2, Typer 0.29.1, Rich 14.3.4.
El modelo comercial del equipo no pudo consultarse: `sysctl` está restringido.

| Formato | Tamaño (bytes) | Primera (s) | Mediana (s) | Máximo (s) | <1 s |
| --- | ---: | ---: | ---: | ---: | --- |
| CSV | 3466243 | 0.1653 | 0.1618 | 0.1775 | 7/7 |
| PARQUET | 38021 | 0.1910 | 0.1446 | 0.1910 | 7/7 |

Salida completa de la ejecución final sobre el código verificado:

```json
{
  "python": "3.12.13 (main, Jun  2 2026, 22:42:28) [Clang 22.1.3 ]",
  "platform": "macOS-27.2.1-arm64-arm-64bit",
  "machine": "arm64",
  "processor": "arm",
  "logical_cpus": 6,
  "versions": {
    "polars": "1.44.2",
    "typer": "0.29.1",
    "rich": "14.3.4"
  },
  "rows": 100000,
  "columns": 6,
  "protocol": "7 fresh processes per format; includes startup, read, analysis and Rich output capture; OS cache not flushed; generation excluded",
  "results": {
    "csv": {
      "bytes": 3466243,
      "seconds": [
        0.1652694589974999,
        0.16248504200120806,
        0.1618384169996716,
        0.16178387499894598,
        0.16053045900116558,
        0.16010233400083962,
        0.17754783300188137
      ],
      "first_seconds": 0.1652694589974999,
      "median_seconds": 0.1618384169996716,
      "max_seconds": 0.17754783300188137,
      "all_under_one_second": true
    },
    "parquet": {
      "bytes": 38021,
      "seconds": [
        0.1909860000014305,
        0.14459279099901323,
        0.13879454099878785,
        0.15955229199971654,
        0.14096254199830582,
        0.14589770800012047,
        0.13927870900079142
      ],
      "first_seconds": 0.1909860000014305,
      "median_seconds": 0.14459279099901323,
      "max_seconds": 0.1909860000014305,
      "all_under_one_second": true
    }
  }
}
```

## Hito 2 — tiempo y memoria (2026-11-14)

Mismo fixture de 100.000 filas y seis columnas, siete procesos nuevos por formato. Comandos: `.venv/bin/python scripts/benchmark.py` y `.venv/bin/python scripts/benchmark.py --ml`. El MVP se extrajo de `93a72a9` (solo `src/`) a un directorio temporal y se ejecutó con ese `src` en `PYTHONPATH`, usando el script de benchmark actual y el mismo entorno. El checkout permaneció intacto. Cachés del SO sin vaciar; no es disco frío.

Entorno: Python 3.12.13, macOS-27.2.1-arm64-arm-64bit, 6 CPU lógicas; Polars 1.44.2, Typer 0.29.1, Rich 14.3.4, NumPy 2.5.4, scikit-learn 1.9.1. Archivos: CSV 3.466.243 bytes, Parquet 38.021 bytes.

| Modo | Formato | Mediana (s) | Máximo (s) | Ejecuciones <1 s | RSS máximo (MiB) |
| --- | --- | ---: | ---: | ---: | ---: |
| MVP 93a72a9 | CSV | 0.1731 | 0.1771 | 7/7 | 120.62 |
| MVP 93a72a9 | PARQUET | 0.1482 | 0.1513 | 7/7 | 115.00 |
| Hito 2 habitual | CSV | 0.1804 | 0.2166 | 7/7 | 121.39 |
| Hito 2 habitual | PARQUET | 0.1560 | 0.1577 | 7/7 | 117.25 |
| Hito 2 --ml | CSV | 0.9358 | 1.0928 | 6/7 | 219.95 |
| Hito 2 --ml | PARQUET | 1.0025 | 1.3578 | 3/7 | 202.31 |

El RSS es el pico del proceso completo (`resource.getrusage(RUSAGE_SELF)`), medido en **un proceso adicional por caso**, fuera de las siete mediciones temporales. Incluye intérprete, librerías, lectura y renderizado; no mide asignaciones exclusivas del detector ni memoria incremental. macOS entrega bytes; el script convierte KiB a bytes en Linux. No se ejecutó en Windows.

El modo habitual añade unos 7–8 ms a las medianas de esta referencia. El RSS habitual subió aproximadamente 1–2 MiB; una observación por caso no permite atribuir esa diferencia pequeña exclusivamente al código. Con ML el pico fue de unos 202–220 MiB. El objetivo de <1 s se cumple para el modo habitual y **no se cumple en todas las ejecuciones con ML**. Estas mediciones locales no garantizan rendimiento general.

IsolationForest evaluó una muestra de 10.000 de las 99.000 filas elegibles en `amount` y `quantity` (100.000 totales). `active` es booleano en ambos formatos y se excluye. No se extrapolan resultados a las filas no evaluadas.

Tiempos completos en segundos (orden de ejecución):

- MVP 93a72a9, CSV: 0.173769, 0.166892, 0.170422, 0.173148, 0.168663, 0.177057, 0.174179.
- MVP 93a72a9, PARQUET: 0.145179, 0.146922, 0.148185, 0.147980, 0.149379, 0.149536, 0.151333.
- Hito 2 habitual, CSV: 0.216647, 0.183059, 0.180442, 0.180766, 0.178411, 0.179283, 0.179043.
- Hito 2 habitual, PARQUET: 0.155354, 0.154395, 0.154412, 0.156472, 0.157702, 0.157689, 0.156000.
- Hito 2 --ml, CSV: 0.881159, 0.935764, 0.867609, 1.092808, 0.949375, 0.984487, 0.917316.
- Hito 2 --ml, PARQUET: 0.870246, 0.934211, 0.973918, 1.357753, 1.340686, 1.310540, 1.002480.

Verificación funcional final: `.venv/bin/python -m pytest -q -W error` — 61 pruebas, sin warnings. El benchmark verifica SHA-256 de los archivos antes/después de las ejecuciones; la suite también verifica CSV/Parquet y la entrada intacta con `--ml`.
