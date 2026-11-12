# Sistema automatizado de clips verticales — Evidencia V5 y V8

Repositorio de evidencia técnica del **primer avance del proyecto de grado** del programa de Ingeniería de Sistemas de la Universidad Nacional Abierta y a Distancia (UNAD).

**Nombre del proyecto:** Sistema automatizado de procesamiento y generación de clips verticales para creadores de contenido digital.

**Línea de investigación:** Infraestructura Tecnológica y Transformación Digital Inteligente.

## Archivos incluidos

- `clipsv5.py`: versión de referencia inicial del creador de reels local, asociada al canal @Línea90Min.
- `clipsv8.py`: evolución del motor local usada como base para el análisis y la refactorización de la fase 1.

## Comparación de código

| Indicador | V5 | V8 |
|---|---:|---:|
| Líneas de código | 3.008 | 3.588 |
| Funciones definidas a nivel principal | 77 | 89 |

En la comparación entre los archivos se identificaron **12 funciones nuevas, 23 modificadas y 54 sin cambios**. No se eliminó ninguna función principal.

La V8 incorpora mecanismos de monitoreo de FFmpeg (watchdog), pruebas de codificadores con respaldo en `libx264`, exportación temporal segura, análisis RMS vectorizado con NumPy y ajustes al diseño vertical de 1080 × 1920 píxeles.

## Alcance de la evidencia

Estos archivos documentan la evolución del prototipo **local**, no una plataforma SaaS terminada. La revisión comparativa es estática: no se adjuntan aquí resultados cuantificados de pruebas de rendimiento ni capturas de ejecución.

Las siguientes tareas previstas incluyen separar componentes del motor en módulos, realizar pruebas funcionales con video y preparar la integración posterior con un backend FastAPI.

## Ejecución y dependencias

Los scripts emplean Python y ejecutables externos **FFmpeg** y **ffprobe**. Algunas funciones adicionales requieren librerías como NumPy, OpenCV y faster-whisper. El flujo también espera archivos gráficos propios del canal (como `logo_canal.png` y `plantilla_intro.png`), que **no están incluidos en esta entrega**.

Por eso, disponer únicamente de estos dos scripts permite revisar el código y compararlo, pero no garantiza una ejecución completa sin configurar el entorno y aportar los recursos gráficos.

## Estado

Material de apoyo para la fase 1: análisis y refactorización del código base. Evidencia de ejecución y validación funcional: pendiente de adjuntar.
