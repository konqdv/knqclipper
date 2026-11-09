# Plan de implementación

Cada fase termina con pruebas automatizadas del módulo y una verificación integrada. El
estado se actualiza al cerrar cada iteración (ver la sección *Estado* al final).

| Fase | Alcance | Criterio de aceptación |
|---|---|---|
| 1 | Arquitectura, esqueleto FastAPI, BD, dashboard, conectores (YouTube, Twitch, Kick, local, VOD, ingest) | `POST /api/channels` con una URL real identifica plataforma y estado live real; dashboard lista canales |
| 2 | Capturador HLS con deduplicación, segmentador FFmpeg, buffer circular con pins, supervisor con reconexión, multistream | Segmentos con tiempo de stream monotónico; reconexión sin duplicados (test de playlist solapada) |
| 3 | Audio (rasgos), ASR incremental con timestamps por palabra, clasificador universal, analizador de contexto (LLM + heurística) | Transcripción en vivo de un audio real con desfase < 2 ventanas |
| 4 | Emoción, ganchos, historias, chat, visión, motor universal, ranker, deduplicación | Momentos con puntuación, subpuntuaciones, motivo, límites sin cortar palabras |
| 5 | Extracción del original, render 1080×1920 H.264/AAC, motor de captions (7 estilos) | Clip reproducible con captions sincronizados palabra a palabra |
| 6 | Reencuadre inteligente (track/split/fit/center/manual), editor con previsualización en vivo | El encuadre sigue al sujeto sin saltos; edición → re-render |
| 7 | Training Library, feedback (aprobar/rechazar/1–5/motivos/recorte), modelo con validación y versiones, preferencias por creador/categoría | Un modelo candidato solo se promueve si mejora en validación |
| 8 | Social Performance Analyzer: publicaciones, métricas (manual/CSV/YouTube Analytics), insights | Insights por duración/gancho/categoría/estilo con tamaño de muestra |
| 9 | Optimización, pruebas de carga multistream, workers Redis | N streams simultáneos sin perder segmentos; render en workers externos |

## Decisiones de alcance de la primera versión

* Todos los módulos existen y son funcionales con implementaciones *ligeras* (DSP,
  léxico, OpenCV, regresión logística) y puntos de extensión para modelos más pesados
  (eventos de audio AudioSet, diarización, expresiones faciales, LLM multimodal).
* El LLM es opcional: sin él, el motor heurístico multiseñal sigue funcionando; con él
  (Ollama u otro proveedor), el análisis contextual se vuelve semántico.
* Métricas sociales: importación manual/CSV para las tres plataformas y cliente de
  YouTube Analytics (OAuth). TikTok e Instagram requieren apps aprobadas por las
  plataformas: se documenta y se deja la interfaz preparada.

## Estado

Ver `README.md` → *Estado del proyecto*.
