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
- Compara columnas y tipos con un archivo anterior mediante `--baseline`.

Los resultados aparecen en tablas de terminal, con conteos, cobertura y motivos
cuando un análisis se omite. Son indicios para revisar en contexto.

## Instalación

Requiere **Python 3.11 o superior**. Desde la raíz del repositorio:

```bash
uv sync
```

También podés instalarlo con pip:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

En Windows, activá el entorno con `.venv\Scripts\Activate.ps1` en PowerShell.

## Uso

```bash
uv run blink datos.csv
uv run blink datos.parquet
```

Para habilitar el análisis con ML:

```bash
uv run blink datos.csv --ml
```

Para comparar con un archivo anterior y ver columnas agregadas, eliminadas o
cambios de tipo:

```bash
uv run blink datos.csv --baseline anterior.csv
```

Ambos archivos pueden ser CSV o Parquet. Se comparan columnas por nombre;
un cambio de orden no genera un aviso. La comparación evalúa el esquema,
sin comparar todavía los valores de las columnas.

Para probarlo con el archivo de ejemplo incluido o consultar la ayuda:

```bash
uv run blink tests/data/sample_dirty.csv
uv run blink --help
```

Si lo instalaste con pip, ejecutá `blink` directamente con el entorno activo.
Para desactivar los colores en macOS/Linux: `NO_COLOR=1 blink datos.csv`.

Los CSV deben usar UTF-8, comas como separador y una primera fila de cabecera.
El archivo debe entrar en memoria. El análisis con ML puede evaluar una muestra;
la salida indica cuántas filas se analizaron.

Los avisos no hacen fallar el comando: devuelve `0` si completó la inspección
y `2` ante un error de uso, lectura o análisis.

Licencia [MIT](LICENSE).
