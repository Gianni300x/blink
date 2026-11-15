# blink

**Un diagnóstico de calidad de datos, a un comando de distancia.**

Blink es una herramienta de terminal que analiza archivos CSV y Parquet locales
y muestra problemas de calidad e indicios de anomalías. No requiere configuración
previa, no envía datos a servicios externos y no modifica tus archivos.

## Qué hace

- Muestra dimensiones y tipos de las columnas.
- Detecta nulos, NaN, filas duplicadas y columnas constantes.
- Señala posibles identificadores de texto.
- Detecta outliers numéricos con IQR.
- Advierte sobre columnas de texto mayormente numéricas o de fechas con valores incompatibles.
- Permite analizar anomalías con IsolationForest mediante `--ml`.
- Compara columnas, tipos y distribuciones con un archivo anterior mediante `--baseline`.

Los resultados aparecen en tablas de terminal, con conteos, cobertura y motivos
cuando un análisis se omite. Son indicios para revisar en contexto.

## Instalación

Requiere **Python 3.11 o superior**. Con [uv](https://docs.astral.sh/uv/getting-started/installation/),
instalá la app en un entorno aislado y comprobá la ayuda:

```bash
uv tool install blink-data-lint
blink --help
```

El paquete se llama **`blink-data-lint`** en PyPI; el comando es **`blink`**.
Si acabás de instalar uv y la terminal no encuentra `blink`, ejecutá
`uv tool update-shell` y abrí una terminal nueva.

<details>
<summary>Alternativas: pip o instalación desde el repositorio</summary>

Con pip, en macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install blink-data-lint
blink --help
```

En Windows, desde PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install blink-data-lint
blink --help
```

Mantené el entorno activo. En una terminal nueva, volvé a activarlo con
`source .venv/bin/activate` (macOS/Linux) o `.venv\Scripts\Activate.ps1` (Windows).

Para trabajar desde el código fuente, necesitás [Git](https://git-scm.com/downloads/):

```bash
git clone https://github.com/Gianni300x/blink.git
cd blink
uv sync
uv run blink tests/data/sample_dirty.csv
```

En este caso, usá `uv run blink` en lugar de `blink` en los ejemplos de abajo.
Con pip desde el repositorio, podés usar `python -m pip install -e .` dentro del
entorno virtual en lugar de instalar el paquete desde PyPI.

</details>

## Uso

```bash
blink datos.csv
blink datos.parquet
```

Reemplazá `datos.csv` o `datos.parquet` por la ruta de tu archivo. Si tiene
espacios, escribila entre comillas: `blink "mis datos/ventas.csv"`.

Para habilitar el análisis con ML:

```bash
blink datos.csv --ml
```

Para comparar con un archivo anterior y detectar cambios de columnas, tipos,
medias, distribuciones o proporción de faltantes:

```bash
blink datos.csv --baseline anterior.csv
```

Ambos archivos pueden ser CSV o Parquet. Se comparan columnas por nombre;
un cambio de orden no genera un aviso. Los valores se comparan de forma agregada,
sin emparejar filas. La salida muestra las métricas, umbrales y cobertura.

Para consultar la ayuda:

```bash
blink --help
```

Para desactivar los colores en macOS/Linux: `NO_COLOR=1 blink datos.csv`.

## Límites de uso

- Archivos locales CSV o Parquet que entren en memoria. CSV requiere UTF-8,
  comas y una primera fila de cabecera; no se adivinan otros separadores.
- IQR necesita al menos 10 valores numéricos finitos. Si el rango intercuartílico
  es cero, se omite el detector y se indica el motivo.
- Los tipos rotos requieren al menos 10 textos no vacíos y una mayoría ≥90 %
  de números finitos o fechas válidas `YYYY-MM-DD`. No se interpretan fechas
  locales como `01/02/2026`.
- ML necesita al menos 32 filas completas y finitas en sus columnas numéricas
  elegibles. Si hay más de 32 columnas elegibles, se omite. Evalúa hasta 10.000
  filas; por encima toma una muestra y muestra su cobertura, sin extrapolar conteos.
- Nombres como `VendorID`, `PULocationID`, `payment_type` o `codigo_postal`
  indican posibles códigos o etiquetas. Se omiten de IQR y ML; al comparar
  archivos se usan frecuencias, no medias o distancias numéricas. Es una
  heurística por nombre y se informa como tal; no demuestra una clave primaria.
- Una anomalía es un indicio, no una prueba de error. Un análisis omitido tampoco
  equivale a un resultado sin problemas. Revisá los motivos que muestra la salida.
- La comparación de valores requiere tipos idénticos y al menos 100 observaciones
  elegibles por archivo. Avisa si la media se desplaza ≥0.5 desviaciones estándar
  del archivo anterior, la distancia de distribución es ≥0.2 (KS en números o
  variación total en categorías), o los faltantes cambian ≥10 puntos porcentuales.
  Son umbrales de revisión; no son pruebas de significación estadística.
  Con desviación anterior cero se omite la comparación de medias, pero se compara
  la distribución. Las categorías se limitan a 50 valores distintos entre ambos
  archivos; IDs/texto libre y tipos no compatibles pueden omitir ese análisis.

## Integración con otros programas y CI

El uso habitual sigue siendo `blink archivo.csv`. Si necesitás un informe para
otro programa, agregá `--json`:

```bash
blink datos.csv --json > informe.json
```

Para hacer fallar una validación automatizada cuando aparezcan avisos:

```bash
blink datos.csv --json --exit-code > informe.json
```

También podés combinarlo con `--baseline anterior.csv` y `--ml`. En un paso de CI,
el código de salida determina si continúa o falla la validación:

| Código | Significado |
| --- | --- |
| `0` | Inspección completada; sin `--exit-code`, los avisos también devuelven `0`. |
| `1` | Hay avisos y se activó `--exit-code`. |
| `2` | Error de uso, lectura o análisis, con o sin `--exit-code`. |

Si preferís ver las tablas en el log de CI, usá `--exit-code` sin `--json`.

Se consideran avisos los hallazgos de calidad, cambios de esquema y métricas de
drift que superan su umbral. Los posibles IDs informativos y los análisis omitidos
no hacen fallar CI por sí solos. Dos métricas alertadas en una columna cuentan
como dos avisos. Un aviso sigue siendo un indicio para revisar en contexto.

El JSON usa `schema_version: "1.0"` e incluye `analysis`, `comparison`, `summary`,
`error` y `exit_code`, además de los archivos y políticas de lectura en `sources`.
`status: "completed"` indica que la ejecución terminó, no que todos los datos sean
correctos. Cada detector indica `completed` o `skipped` y su cobertura. Los
errores de lectura/análisis generan `status: "error"` y un diagnóstico por stderr,
sin informe parcial. Los errores de sintaxis del comando se muestran por stderr
sin generar un documento JSON; comprobá siempre el código de salida.

Si clonaste el repositorio, podés reproducir un fallo de validación con su CSV de ejemplo:

```bash
blink tests/data/sample_dirty.csv --json --exit-code > informe.json
```

Este ejemplo devuelve `1` porque contiene problemas conocidos; el informe JSON
queda disponible para revisar los avisos.

Licencia [MIT](https://github.com/Gianni300x/blink/blob/main/LICENSE).
