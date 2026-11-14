# Hoja de ruta

Estado: **Hitos 1 y 2 completados y verificados**, Hito 2 el 2026-11-14:
61 pruebas pasan con warnings como errores. Benchmark de 100.000 filas bajo 1 s
en CSV y Parquet sin ML (7/7 por formato); con `--ml` hubo ejecuciones >1 s.
Hito 3 sin iniciar. Evidencia y límites en [BENCHMARK.md](BENCHMARK.md).

## Hito 1 — MVP: salud básica y UX en terminal

### Entregables

- [x] Leer CSV y Parquet locales mediante Polars, sin configuración obligatoria.
- [x] Mostrar dimensiones y tipos inferidos del dataset.
- [x] Calcular cantidad y porcentaje de nulos por columna y en el conjunto.
- [x] Contar duplicados exactos como filas excedentes respecto de las filas únicas.
- [x] Detectar columnas constantes; distinguirlas de columnas completamente nulas.
- [x] Alertar cardinalidad sospechosa en texto como posible ID, con heurística explícita.
- [x] Presentar resumen y hallazgos en paneles/tablas Rich, sin depender solo del color.

### Definition of Done

- [x] Un CSV y su equivalente Parquet producen las mismas métricas de salud.
- [x] Fixtures con resultados esperados verifican nulos, duplicados, constantes e IDs.
- [x] Se documentan denominadores, tratamiento de NaN, vacíos y umbrales de cardinalidad.
- [x] Cero filas, cero columnas, solo nulos y entradas inválidas no producen divisiones
  por cero ni diagnósticos engañosos; errores normales no muestran traceback.
- [x] La salida funciona en terminal estrecha y sin color; la entrada conserva sus bytes.
- [x] Se registra un benchmark de extremo a extremo sobre un dataset acordado,
  indicando resultado frente al objetivo de <1 s y sus límites.
- [x] Pytest pasa y el README describe únicamente el comportamiento disponible.

## Hito 2 — El cerebro Data/ML

### Entregables

- [x] Detectar outliers numéricos con IQR y/o Z-score modificado, con política documentada.
- [x] Incorporar IsolationForest ligero, reproducible y con trabajo acotado.
- [x] Detectar tipos rotos: columnas de texto mayormente numéricas o fechas que
  contienen valores incompatibles como `N/A` o `error`.
- [x] Mostrar método, cobertura, conteos y umbrales para interpretar cada alerta.

### Definition of Done

- [x] Fixtures deterministas verifican outliers conocidos y tipos rotos numéricos/de fecha.
- [x] Constantes, MAD/IQR cero, nulos, NaN, infinitos y muestras insuficientes se
  manejan explícitamente; los detectores omitidos indican la razón.
- [x] Los valores basura siguen disponibles para el diagnóstico: no se descartan
  silenciosamente durante la inferencia o el parseo de lectura.
- [x] Semilla, tamaño de muestra, límite de filas y parámetros de IsolationForest
  quedan documentados; se informa si el resultado cubre solo una muestra.
- [x] Se revisan falsos positivos en IDs y fechas ambiguas; no se alteran los datos
  ni se afirma conocer la intención de negocio.
- [x] La conversión a NumPy se limita a lo necesario; se mide el impacto temporal
  y de memoria respecto del MVP, sin cargar scikit-learn al arrancar la CLI básica.
- [x] Pytest pasa y la documentación explica los límites de los métodos.

## Hito 3 — Data drift y producción

### Entregables

- [ ] Añadir `--baseline <archivo>` para comparar con un CSV/Parquet anterior.
- [ ] Alertar columnas añadidas/eliminadas y cambios de tipos.
- [ ] Alertar desviaciones bruscas en medias/distribuciones con métricas y umbrales explícitos.
- [ ] Añadir `--json` con contrato de salida versionado y sin decoración de Rich.
- [ ] Añadir `--exit-code` para habilitar fallos de CI/CD por hallazgos.

### Definition of Done

- [ ] Datasets idénticos, cambios de esquema y drift sintético tienen pruebas reproducibles.
- [ ] Se define el tratamiento de columnas incompatibles, constantes, vacías y no comparables.
- [ ] Se documentan métrica de distribución, normalización y umbrales antes de implementarlos.
- [ ] JSON es parseable, incluye versión de esquema y distingue hallazgos, análisis
  omitidos y errores; diagnósticos operativos van a stderr.
- [ ] Se documenta y prueba la política propuesta: 0 para ejecución completa sin
  fallo habilitado, 1 para hallazgos de severidad acordada con `--exit-code`, 2 para
  errores de uso/lectura/ejecución. Los errores fallan también sin `--exit-code`.
- [ ] Ambos archivos permanecen intactos y existe un ejemplo reproducible de CI.
- [ ] Pytest pasa y se repite el benchmark indicando el costo de baseline y ML.

## Decisiones adoptadas para el Hito 1

- [x] Aplicar responsabilidades y contratos de ARCHITECTURE.md.
- [x] Documentar política CSV: inferencia, nulos, strings vacíos y valores especiales.
- [x] Adoptar heurística de posibles IDs (≥10 textos, ≥95 % únicos) y severidades.
- [x] Fijar fixture de 100.000 filas, protocolo y resultados en BENCHMARK.md.

La solicitud de implementar el Hito 1 autorizó avanzar sobre la arquitectura
propuesta. Los valores predeterminados están documentados en README.md y son
reversibles; no requieren configuración previa del usuario.

Verificación ejecutada con `.venv/bin/python -m pytest -q` (36 passed),
`.venv/bin/blink tests/data/sample_dirty.csv` y
`.venv/bin/python scripts/benchmark.py`. La equivalencia CSV/Parquet, los casos
límite, la conservación de bytes y el reporter sin color están cubiertos por tests.
Esta evidencia corresponde al Hito 1; la verificación posterior del Hito 2 se detalla abajo.

## Decisiones y verificación del Hito 2

La continuación del 2026-11-14 implementa IQR y tipos rotos por defecto, e
IsolationForest opt-in con `--ml`. Los umbrales y omisiones están en README.md.
Se elige IQR sin fallback a MAD; IQR cero se informa como omitido. Fechas: solo
ISO de calendario. ML: semilla 42, hasta 10.000 filas y 32 columnas, sin imputar
ni extrapolar los conteos de la muestra.

Verificado con `.venv/bin/python -m pytest -q -W error` (61 passed), CLI instalada
con `--ml`, benchmark con y sin ML, y referencia del MVP ejecutada desde una copia
temporal de `93a72a9` con el mismo entorno. Se mide RSS máximo en un proceso
adicional por caso, sin modificar el checkout ni los archivos de entrada.
Python 3.11 sigue sin verificación de ejecución; no se afirma una matriz de versiones.
