# Fronteras del proyecto

Estado: fronteras vigentes para la implementación autorizada del Hito 1. Este documento define el alcance
de `blink`; el [roadmap](ROADMAP.md) determina el orden de implementación.

## Qué ES blink

Un escáner rápido de terminal y linter de calidad de datos tabulares. Un
desarrollador o analista ejecuta `blink archivo.csv` o `blink archivo.parquet` y
obtiene un diagnóstico visual inmediato, comprensible y accionable.

- Zero-config: no exige archivos YAML/JSON, reglas declarativas ni entrenamiento
  previo. Los valores predeterminados deben estar documentados.
- Archivos locales CSV y Parquet; procesamiento con Polars y NumPy.
- Diagnóstico de salud básica y detección estadística/ML ligera con scikit-learn.
- Interfaz Typer y presentación Rich, con mensajes y severidades legibles sin color.
- Ejecución local y de solo lectura: no modifica, limpia ni reescribe el archivo
  de entrada ni el baseline. No envía datos a servicios externos.
- Python 3.11+ y empaquetado con `pyproject.toml`, compatible con uv y pip; Pytest
  verifica contratos y casos límite.

## Qué NO ES blink

- No es un orquestador de pipelines, scheduler ni sistema ETL.
- No reemplaza Great Expectations, dbt ni otros frameworks de validación pesados.
- No transforma, corrige, imputa, elimina ni exporta una versión limpia de datos.
- No requiere bases de datos externas, servidores ni cuentas de usuario.
- No incluye dashboard web, API de servicio, conectores remotos ni monitoreo continuo.
- No garantiza que una anomalía estadística sea un error de negocio. Los posibles
  IDs y tipos rotos son indicios, no certezas semánticas.

## Rendimiento y honestidad del diagnóstico

Menos de un segundo es un objetivo de producto, no una garantía para cualquier
tamaño de archivo, hardware o detector. Antes de declararlo cumplido se fijarán
dataset, formato, tamaño, hardware, versiones y protocolo de medición. La medida
incluye arranque del proceso, lectura, análisis y renderizado.

La salud básica debe ser exacta sobre el archivo completo. Cualquier muestreo o
límite aplicado a ML se informará, junto con la semilla y la cobertura. Un análisis
omitido o un error de lectura nunca se mostrará como un resultado saludable.

## Control de alcance

El desarrollo se divide exclusivamente en los tres hitos de ROADMAP.md. La solicitud posterior de implementar el Hito 1 autoriza su desarrollo sobre
la arquitectura propuesta. El hito solo se declara completo con sus criterios
de aceptación verificados. Las adiciones fuera de estas fronteras requieren una nueva
decisión explícita de alcance.
