# blink

**Un diagnóstico de calidad de datos, a un comando de distancia.**

Blink es una herramienta de terminal que analiza archivos CSV, TSV y Parquet locales
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

El informe empieza con un resumen y los avisos que conviene revisar, antes de
las tablas y los detalles técnicos. Con `--baseline`, el resumen incluye los
avisos de comparación y distingue una comparación parcial o limitada de una
que terminó sin cambios observados. Los motivos de omisión y la cobertura
quedan en el detalle. Un aviso cuenta una regla o métrica; varios avisos pueden
referirse a las mismas filas. Los avisos incluyen hasta tres ejemplos con su
número de registro original y los valores observados. Son indicios para revisar
en contexto.

Los registros empiezan en **1, sin contar la cabecera**. En CSV, una celda puede
contener saltos de línea: el número de registro no es una línea física del archivo.
Los valores se muestran escapados y se truncan a 100 caracteres. Duplicados y ML
muestran hasta tres columnas por ejemplo; los demás avisos muestran la columna
afectada. Los ejemplos son parciales; el conteo conserva la cobertura indicada.

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
blink datos.tsv
blink datos.parquet
```

Reemplazá el nombre de ejemplo por la ruta de tu archivo. Si tiene
espacios, escribila entre comillas: `blink "mis datos/ventas.csv"`.

Si los números de la exportación usan coma decimal, indicá ese formato:

```bash
blink ventas.csv --decimal-comma
```

Esto lee `12,50` como `12.5`. Usalo en CSV y TSV con una sola convención
decimal y sin separadores de miles. Si la coma también separa las columnas,
el valor decimal tiene que estar entre comillas, como `"12,50"`.

Para habilitar el análisis **exploratorio** con ML:

```bash
blink datos.csv --ml
```

Para comparar con un archivo anterior y detectar cambios de columnas, tipos,
medias, distribuciones o proporción de faltantes:

```bash
blink datos.csv --baseline anterior.csv
```

Ambos archivos pueden ser CSV, TSV o Parquet. Si usás `--decimal-comma`,
se aplica a ambos archivos de texto. Parquet usa los tipos que ya trae.
Para comparar archivos de texto con distintas convenciones decimales,
reexportalos con la misma convención. Se comparan columnas por nombre;
un cambio de orden no genera un aviso. Los valores se comparan de forma agregada,
sin emparejar filas. La salida muestra las métricas, umbrales y cobertura.

En una terminal interactiva, Blink muestra la etapa actual y avance por columnas
cuando puede medirlo. El indicador desaparece al terminar, se escribe por stderr
y se desactiva en logs o CI sin terminal; `--json` conserva stdout limpio.

Para consultar la ayuda:

```bash
blink --help
```

Para desactivar los colores en macOS/Linux: `NO_COLOR=1 blink datos.csv`.

## Límites de uso

- Archivos locales CSV, TSV o Parquet. La lectura y los agregados se procesan por
  columnas y lotes; se relee el archivo para reducir la memoria. Los CSV de
  16 MiB o más se convierten una vez a una copia temporal local Parquet para
  evitar interpretar el texto en cada pasada; la política de lectura lo indica.
  Los conteos de únicos/duplicados y los cálculos exactos de una columna todavía necesitan
  memoria proporcional a sus datos. No se garantiza RAM constante ni capacidad
  para cualquier archivo mayor que la memoria disponible.
- CSV y TSV requieren cabecera con nombres de columnas únicos. En CSV se detectan
  coma, punto y coma, tabulador o barra `|`. En TSV se usa tabulador.
  Se respetan las comillas. La lectura completa valida UTF-8; se reconocen BOM UTF-8
  y UTF-16. Si UTF-8 falla, se intenta Windows-1252 y se informa como codificación
  estimada; no se reemplazan bytes ilegibles. Codificaciones indistinguibles no
  pueden confirmarse automáticamente: comprobá los acentos de los ejemplos.
  La detección usa hasta 131.072 caracteres iniciales; la cabecera completa debe
  entrar en esa muestra. Las celdas de datos pueden superar ese tamaño.
  Se comprueba el número de campos de todos los registros. Se aceptan saltos LF,
  CRLF o CR. No se pueden mezclar saltos CR con LF o CRLF entre registros.
  Una estructura ambigua o inválida se rechaza. Para resolverla, exportá como
  UTF-8 con un único separador y campos entre comillas. Si Blink detecta números
  con coma en texto, lo indica en el detalle del detector. Revisá la convención
  del archivo antes de activar `--decimal-comma`.
  Las conversiones de codificación usan una copia
  temporal local UTF-8. Esa copia y el Parquet temporal de CSV grandes se
  eliminan al terminar; necesitan espacio libre en disco.
- IQR necesita al menos 10 valores numéricos finitos. Si el rango intercuartílico
  es cero o la columna tiene enteros fuera de ±2^53, se omite el detector y se
  indica el motivo.
- Los tipos rotos requieren al menos 10 textos no vacíos y una mayoría ≥90 %
  de números finitos o fechas válidas `YYYY-MM-DD`. No se interpretan fechas
  locales como `01/02/2026`.
- ML necesita al menos 32 filas completas y finitas en sus columnas numéricas
  elegibles. Si hay más de 32 columnas elegibles, se omite. Evalúa hasta 10.000
  filas; por encima selecciona por hash del número de registro con semilla fija,
  antes de materializar la muestra. Muestra su cobertura sin extrapolar conteos.
  La muestra se reproduce con la misma entrada y versiones de dependencias;
  puede cambiar respecto de versiones anteriores de Blink. ML detecta rareza,
  no valida que una operación de negocio sea correcta. Su aporte depende del
  dataset y puede repetir avisos ya detectados por las reglas. Se excluyen de ML los
  posibles contadores de fila: columnas enteras llamadas `index`, `row_index`,
  `row_number` y variantes habituales de exportación, sin nulos y consecutivas
  desde 0 o 1. La omisión se informa; no se excluye cualquier medida creciente.
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

Los informes de terminal y JSON incluyen pequeños extractos de tus datos locales;
consideralo antes de compartirlos o guardarlos en logs de CI.

Para omitir esos ejemplos de filas y sus valores:

```bash
blink datos.csv --no-examples
blink datos.csv --json --no-examples > informe.json
```

Se mantienen los avisos, conteos, nombres de columnas, rutas y métricas como
los umbrales IQR o los agregados de comparación. Esta opción no anonimiza
el informe completo ni modifica el análisis. También funciona con `--ml`,
`--baseline` y `--exit-code`. En JSON, los campos `examples` quedan vacíos.

El JSON usa `schema_version: "1.1"` e incluye `analysis`, `comparison`, `summary`,
`error` y `exit_code`, además de los archivos y políticas de lectura en `sources`.
`status: "completed"` indica que la ejecución terminó, no que todos los datos sean
correctos. Cada hallazgo y detector incluye `examples`, con `row` (registro desde 1) y
`values` (valores como texto escapado). Cada detector indica `completed` o
`skipped` y su cobertura. Los errores de lectura/análisis generan `status: "error"` y un diagnóstico por stderr,
sin informe parcial. Los errores de sintaxis del comando se muestran por stderr
sin generar un documento JSON; comprobá siempre el código de salida.

Si clonaste el repositorio, podés reproducir un fallo de validación con su CSV de ejemplo:

```bash
blink tests/data/sample_dirty.csv --json --exit-code > informe.json
```

Este ejemplo devuelve `1` porque contiene problemas conocidos; el informe JSON
queda disponible para revisar los avisos.

Licencia [MIT](https://github.com/Gianni300x/blink/blob/main/LICENSE).
