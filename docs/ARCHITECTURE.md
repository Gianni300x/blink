# Arquitectura modular

Estado: arquitectura aplicada a los Hitos 1 y 2. Lectura, análisis, detectores y
presentación están implementados y verificados en Python 3.12.13.

## Estructura

```text
.
├── pyproject.toml
├── README.md
├── LICENSE
├── docs/
│   ├── PROJECT_CHARTER.md
│   ├── ROADMAP.md
│   └── ARCHITECTURE.md
├── src/blink/
│   ├── __init__.py
│   ├── cli.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── analyzer.py
│   │   └── detectors.py
│   ├── io/
│   │   ├── __init__.py
│   │   └── loader.py
│   └── ui/
│       ├── __init__.py
│       └── reporter.py
└── tests/
    ├── test_cli.py
    └── data/sample_dirty.csv
```

## Responsabilidades y límites

| Módulo | Responsabilidad | Fuera de su responsabilidad |
| --- | --- | --- |
| `cli.py` | Argumentos Typer, coordinación y códigos de salida | Cálculos, heurísticas o formato visual |
| `io/loader.py` | Lectura CSV/Parquet con Polars, inferencia y errores contextualizados | UI, ML o escritura de datasets |
| `core/analyzer.py` | Métricas de salud y composición del informe | Abrir rutas, imprimir, decidir códigos de proceso |
| `core/detectors.py` | Estadística robusta, IsolationForest y tipos rotos (Hito 2) | Leer archivos o renderizar |
| `core/models.py` | Contratos de resultados neutrales a Typer/Rich | Presentación o carga |
| `ui/reporter.py` | Paneles y tablas Rich a partir de resultados | Analizar datos o cambiar umbrales |

La CLI recibe un archivo, solicita su lectura, invoca el analizador y entrega el
resultado al reporter. El analizador utiliza detectores cuando corresponda. Los
contratos de `models.py` son compartidos, sin dependencia de la UI. `core` no
importa `io`, `ui` ni `cli`; `io` y `ui` tampoco se importan entre sí.

La CLI valida la ruta/extensión, carga el archivo, analiza su DataFrame y entrega
el informe al reporter. Los errores de lectura y de operación de Polars se muestran
sin traceback ni volcado de valores de celdas y terminan con código 2.

## Contratos del Hito 1

- **Lectura:** una ruta produce un DataFrame Polars y metadatos de lectura
  (formato, política de inferencia y advertencias). Un fallo produce un error
  contextualizado; nunca un DataFrame vacío usado para ocultarlo.
- **Análisis:** un DataFrame y opciones internas con valores predeterminados
  producen un informe estructurado. No requieren un archivo de configuración.
- **Informe:** dimensiones, esquema, métricas por columna, hallazgos y análisis
  omitidos. Un hallazgo contiene identificador estable, severidad, columna
  opcional, mensaje y evidencia numérica; la cobertura distingue exacto/muestreado.
- **Detectores:** reciben datos en memoria y devuelven resultados estructurados,
  incluyendo método, parámetros y motivo si no es posible ejecutar el análisis.
- **Presentación:** recibe un informe y una consola Rich inyectable y escribe solo
  la representación. No vuelve a calcular métricas.

`LoadedDataset` (en `io/loader.py`) agrupa DataFrame, formato y política de lectura.
`AnalysisReport`, `ColumnMetrics` y `Finding` son dataclasses inmutables en
`core/models.py`; no dependen de Polars ni Rich. Cobertura exacta y códigos estables
identifican hallazgos. `DetectorResult` agrega método, columnas, filas totales,
elegibles y evaluadas, conteo opcional, parámetros y motivo de omisión. Un conteo
`None` significa análisis omitido; cero significa ejecutado sin indicios.
`AnalysisReport.detectors` reúne esos resultados; los hallazgos conservan códigos
estables (`numeric_outliers`, `broken_type`, `isolation_outliers`). La evidencia
numérica del Hito 2 vive en los resultados de detector, sin valores de celdas.
La serialización queda para el Hito 3.
Los porcentajes sin denominador se representan con `None`, mostrado como `n/d`.

## Datos y rendimiento

Polars mantiene el procesamiento columnar. Las métricas de salud operan sobre
todos los registros; no se muestrean silenciosamente. No se incorpora pandas.
NumPy se usa para operaciones numéricas puntuales y la frontera con scikit-learn.
Las importaciones de ML se difieren hasta ejecutar el detector para reducir el
costo del arranque y del análisis básico.
`analyze(frame, ml=False)` ejecuta IQR y tipos rotos; `ml=True`, coordinado por
`--ml`, habilita IsolationForest. Este opt-in conserva el arranque ligero; el
detector desactivado queda registrado como omitido. NumPy solo materializa hasta
10.000 × 32 valores, después del filtrado y muestreo en Polars. La tabla filtrada
en Polars sí puede cubrir todas las filas; el archivo debe entrar en memoria.
Política completa de umbrales, exclusiones y límites en el README.

La lectura debe preservar evidencia de tipos rotos. No se usará `ignore_errors`
ni una conversión a nulos que esconda valores incompatibles. Las convenciones de
nulos, NaN, fechas y strings vacíos están documentadas en README.md. No se admiten
escrituras en los archivos de entrada.

## Evolución dentro del alcance

En el Hito 3, un módulo de comparación en `core` recibirá dos datasets ya cargados;
no mezclará lectura y drift. JSON se añadirá como serializador separado de Rich,
usando el mismo informe. La CLI decidirá los códigos de salida conforme a una
política documentada, no mediante inspección del texto mostrado.

## Verificación

Pytest comprobará cálculos y detectores con datasets pequeños de resultados
conocidos, equivalencia CSV/Parquet y conservación de los archivos. Las pruebas
de CLI usarán `typer.testing.CliRunner`; la UI se podrá capturar con una consola
Rich en memoria. El benchmark será una medición separada de extremo a extremo,
incluido arranque, sin aserciones de tiempo frágiles en tests unitarios.

Referencias técnicas: [entry points de PyPA](https://packaging.python.org/en/latest/specifications/entry-points/)
y [validación de rutas de Typer](https://typer.tiangolo.com/tutorial/parameter-types/path/).

## Verificación ejecutada

61 pruebas pasan con warnings tratados como errores en Python 3.12.13 con Polars 1.44.2. Cubren los cálculos de salud,
los límites de la heurística de IDs, nulos/NaN, duplicados excedentes, datasets vacíos,
lectura CSV/Parquet, evidencia de valores basura, errores de entrada, conservación
de archivos, CLI y UI en 40/80/120 columnas sin color. La invocación instalada
`blink tests/data/sample_dirty.csv` también se verificó.
Los detectores agregan fixtures deterministas de IQR y tipos rotos, umbrales,
fechas imposibles y ambiguas, IDs con ceros iniciales, IQR cero, no finitos,
overflow, omisiones ML, reproducibilidad, límite de filas/columnas, equivalencia
CSV/Parquet y conservación de archivos con `--ml`. Un subproceso verifica que
el análisis habitual no importe NumPy ni sklearn.

El benchmark del Hito 2 sobre 100.000 filas y seis columnas pasó el umbral de <1 s
en las siete ejecuciones de ambos formatos sin ML; con ML hubo ejecuciones por
encima del objetivo. Se compara tiempo y RSS con el MVP. Protocolo y resultados
en [BENCHMARK.md](BENCHMARK.md); script en `scripts/benchmark.py`.
La compatibilidad sintáctica con Python 3.11 se revisó con AST; no se ejecutó una
matriz de versiones de Python ni una revisión de todos los tipos Parquet posibles.
