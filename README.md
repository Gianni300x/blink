# blink

**Un diagnóstico de calidad de datos, a un comando de distancia.**

`blink` es un linter de datos tabulares para la terminal: su objetivo es detectar
problemas de calidad y anomalías en CSV y Parquet, sin configuración previa ni
servicios externos. Construido con Python 3.11+, Polars, NumPy, scikit-learn, Typer
y Rich. Licencia [MIT](LICENSE).

## Estado actual

Hito 1 implementado y verificado con 36 pruebas en Python 3.12.13. `blink`
lee CSV/Parquet y muestra dimensiones, tipos, nulos, NaN, duplicados excedentes,
columnas constantes y posibles IDs de texto. No modifica el archivo original.

```console
blink datos.csv
blink datos.parquet
```

El [benchmark reproducible](docs/BENCHMARK.md) de 100.000 filas y seis columnas
terminó en menos de un segundo en las siete ejecuciones de cada formato, incluyendo
arranque, lectura, análisis y renderizado. Es una medición local, no una garantía
para cualquier archivo o equipo. Python 3.11 es el mínimo declarado; las pruebas
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

Las métricas cubren el archivo completo, sin muestreo. Nulos, NaN, duplicados,
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

## Qué viene después

Hito 2: anomalías numéricas y tipos rotos. Hito 3: comparación con baseline y CI.
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
