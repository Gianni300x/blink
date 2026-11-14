# blink

**Un diagnóstico de calidad de datos, a un comando de distancia.**

`blink` es un linter de datos tabulares para la terminal: su objetivo es detectar
problemas de calidad y anomalías en CSV y Parquet, sin configuración previa ni
servicios externos. Construido con Python 3.11+, Polars, NumPy, scikit-learn, Typer
y Rich. Licencia [MIT](LICENSE).

## Estado actual

Hitos 1 y 2 implementados y verificados con 61 pruebas en Python 3.12.13. `blink`
lee CSV/Parquet y muestra dimensiones, tipos, nulos, NaN, duplicados excedentes,
columnas constantes y posibles IDs de texto, además de outliers IQR y posibles
tipos rotos. IsolationForest se habilita con `--ml`. No modifica el archivo original.

```console
blink datos.csv
blink datos.parquet
blink datos.csv --ml
```

El [benchmark reproducible](docs/BENCHMARK.md) de 100.000 filas y seis columnas
terminó en menos de un segundo en las siete ejecuciones de cada formato sin ML,
incluyendo arranque, lectura, análisis y renderizado. Con `--ml`, algunas ejecuciones
superaron un segundo; se documentan tiempos y memoria frente al MVP.
Es una medición local, no una garantía para cualquier archivo o equipo.
Python 3.11 es el mínimo declarado; las pruebas
se ejecutaron en 3.12, no en una matriz de versiones.

## Instalación desde el repositorio

Requiere Python 3.11 o superior. Ejecutá los comandos desde la carpeta que contiene
`pyproject.toml`. No se presupone una distribución publicada en PyPI.

Con `uv`:

```bash
uv sync --extra dev
uv run blink tests/data/sample_dirty.csv
uv run blink --help
```

Con `pip` (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
blink tests/data/sample_dirty.csv
```

En Windows, activá el entorno con `.venv\Scripts\Activate.ps1` en PowerShell.

## Cómo interpretar el diagnóstico

- **Nulos:** se cuentan los `null` de Polars. Por columna, el denominador es el
  número de filas; en el conjunto, filas × columnas. Sin celdas, se muestra `n/d`.
- **NaN:** se cuentan por separado en columnas flotantes; no se suman a los nulos.
  Los infinitos son valores observados, no faltantes.
- **Duplicados exactos:** filas excedentes respecto de las filas únicas sobre
  todas las columnas ya parseadas. Tres filas iguales cuentan como dos duplicados.
  Null y NaN repetidos se comparan según la igualdad de Polars. En CSV, la inferencia
  puede normalizar representaciones como `01` y `1` al mismo valor numérico.
- **Constantes:** un único valor distinto observado, excluyendo null y NaN.
  También se informa si tiene nulos. Una columna solo null se distingue de una
  constante; una columna solo NaN tampoco es constante. No se calcula varianza.
- **Posible ID:** solo texto, con al menos 10 valores no nulos ni vacíos y una
  proporción únicos/observados ≥95 %. Es informativo: nombres y texto libre también
  pueden cumplir la regla. No demuestra una clave primaria ni exige ausencia de nulos.

Las métricas de salud cubren el archivo completo, sin muestreo. Nulos, NaN, duplicados,
constantes y datasets vacíos generan avisos; posibles IDs generan información.
Los avisos no cambian el código de salida: 0 indica inspección completada y 2 un
error de uso/lectura/análisis. Un archivo vacío sin cabecera es un error; una
cabecera sin filas se informa como dataset sin datos, con porcentajes `n/d`.

### Política de lectura

CSV: UTF-8 estricto (también BOM), delimitador coma y primera fila como cabecera.
La inferencia revisa **todo** el archivo, sin descartar errores de parseo. Un campo
sin valor se considera null; el texto vacío entre comillas se conserva como texto
cuando corresponde al tipo de columna. `N/A`, `null`, espacios y `error` no se
convierten explícitamente en nulos. Fechas quedan como texto. Esto preserva evidencia
para los futuros detectores de tipos rotos. No se adivinan otros delimitadores ni
codificaciones. Archivos corruptos producen un error, sin traceback ni valores de
celdas expuestos en el mensaje.

Parquet: conserva el esquema almacenado y sus nulos/NaN. Los tipos anidados que
Polars no pueda comparar producen un error explícito; no se omiten columnas.
El archivo debe entrar en memoria; el MVP no es un motor de streaming.

La salida se adapta a terminales estrechas y mantiene etiquetas legibles sin color.
Para desactivar color explícitamente: `NO_COLOR=1 blink datos.csv` (macOS/Linux).

### Anomalías y tipos rotos (Hito 2)

- **IQR:** enteros y flotantes, al menos 10 valores finitos. Cuartiles lineales;
  valores estrictamente fuera de `[Q1 − 1.5 × IQR, Q3 + 1.5 × IQR]` son indicios.
  Excluye null, NaN e infinitos; informa filas totales, elegibles y evaluadas,
  conteo y cotas. IQR cero (incluidas constantes) o cotas no representables
  omiten el análisis con motivo; no se usa un fallback de MAD.
- **Tipos rotos:** al menos 10 textos no vacíos después de quitar espacios para
  el diagnóstico. Si ≥90 % son números finitos o fechas ISO `YYYY-MM-DD` válidas,
  informa los incompatibles. Null y textos vacíos no participan. `N/A`, `error`,
  fechas imposibles y texto no finito sí cuentan como incompatibles. No cambia
  los valores ni el esquema. Fechas con barras, timestamps y formatos locales
  quedan fuera; no se adivina día/mes. Códigos numéricos con ceros iniciales
  omiten la candidatura numérica para reducir falsos positivos de IDs.
- **IsolationForest (`--ml`):** numéricas variables con ≥10 valores finitos por
  columna; requiere ≥32 filas completas y finitas en esas columnas, sin imputación.
  Más de 32 columnas elegibles omite el detector completo. Hasta 10.000 filas se
  evalúan completas; por encima se toma una muestra uniforme sin reemplazo,
  semilla 42. Entrena y evalúa únicamente esas filas: no extrapola el conteo.
  Usa 64 árboles, `max_samples=min(256, filas evaluadas)`, `contamination='auto'`,
  `random_state=42`, `n_jobs=1`; decisión `<0` indica anomalía.
  Se escala cada columna de la muestra por `max(abs máximo, 1)` antes del
  float32 interno; si toda la variación desaparece, se omite con motivo.
  Solo la muestra pasa a NumPy; NumPy y sklearn no se importan en el análisis habitual.

IQR y ML omiten posibles IDs enteros llamados `id` o terminados en `_id` con
≥95 % de unicidad entre los finitos. Otros IDs pueden generar falsos positivos;
la regla tampoco demuestra que una columna sea un ID. Booleanos, decimales y
tipos temporales nativos no se usan como variables numéricas en estos detectores.

Cada detector muestra su cobertura y los motivos de omisión. Un conteo cero se
distingue de un análisis omitido. Estas heurísticas señalan casos para revisar;
no prueban errores de negocio. IsolationForest con contaminación automática
puede marcar una proporción considerable incluso en datos válidos.
Los avisos de anomalías conservan el código de salida 0.

## Qué viene después

Hito 3: comparación con baseline y CI.
Los flags `--baseline`, `--json` y `--exit-code` todavía no están disponibles.

## Desarrollo y alcance

```bash
uv run pytest
# Alternativa dentro del entorno instalado con pip:
python -m pytest
```

- [Fronteras del proyecto](docs/PROJECT_CHARTER.md)
- [Roadmap y criterios de aceptación](docs/ROADMAP.md)
- [Arquitectura y contratos propuestos](docs/ARCHITECTURE.md)

Para contribuir, proponé cambios dentro de las fronteras documentadas; un nuevo
objetivo requiere revisión explícita del alcance. Los criterios aún no verificados
siguen abiertos en el roadmap.
