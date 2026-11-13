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
