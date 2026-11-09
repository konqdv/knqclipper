# Arquitectura técnica — Universal AI Live Clipper

## 1. Principios

1. **Live-first.** La unidad de trabajo es el *segmento* (1–6 s) que llega del directo. Todo
   el análisis es incremental; nunca se espera al final de la transmisión.
2. **La captura nunca se bloquea.** Captura, análisis y render corren en tareas/hilos/procesos
   distintos y se comunican por colas acotadas. Si el análisis se retrasa, se degrada (se
   saltan fotogramas de visión) en lugar de frenar la descarga.
3. **Universal, no gaming-céntrico.** Ninguna regla depende de una categoría. La categoría
   detectada solo *reajusta pesos*; todas las señales se siguen evaluando siempre.
4. **Verdad del contenido.** Ningún módulo inventa frases: los ganchos, títulos y límites se
   validan contra la transcripción real con timestamps.
5. **Honestidad del estado.** Un stream solo aparece como "procesando" si hay una conexión
   real entregando segmentos; el estado se deriva de los segmentos recibidos.
6. **Local por defecto.** SQLite, cola en proceso, modelos locales. PostgreSQL, Redis y
   proveedores externos de IA son opcionales por configuración.

## 2. Vista de procesos

```
┌──────────────────────── clipper (FastAPI + asyncio) ───────────────────────┐
│                                                                            │
│  REST + WebSocket ──► EventBus ◄────────────────────────────────┐          │
│        │                                                         │          │
│        ▼                                                         │          │
│  StreamManager ── por canal ──► StreamSupervisor                 │          │
│                                   │  (detecta live, reintenta)   │          │
│                                   ▼                              │          │
│                              StreamSession                       │          │
│   Connector ─► Capture ─► RollingBuffer ─► SegmentFanout         │          │
│   (HLS/ffmpeg)            (disco + índice)   │                   │          │
│                                              ├─► AudioPipeline ──┤ (hilo)   │
│                                              │    PCM → rasgos → emoción    │
│                                              │    PCM → ASR (pool de hilos) │
│                                              ├─► VisualAnalyzer (hilo)      │
│   ChatClient ─► ChatAnalyzer ────────────────┤                              │
│                                              ▼                              │
│                                   HighlightEngine (tick 2 s)                │
│                       disparadores + escaneo semántico periódico            │
│                                              ▼                              │
│                                   MomentEvaluator                           │
│        ContentClassifier · HookDetector · StoryDetector · ContextAnalyzer   │
│                     (LLM opcional) · HighlightRanker · Dedup                │
│                                              ▼                              │
│                      Moment ─► ClipGenerator.extract_source (stream copy)   │
│                                              ▼                              │
│                     Autopilot ≥ umbral ─► JobQueue ◄── Review: aprobación   │
│                                              ▼                              │
│                      RenderWorker(s): reframe + captions + FFmpeg           │
└────────────────────────────────────────────────────────────────────────────┘
           │ opcional: CLIPPER_QUEUE=redis
           ▼
   clipper-worker render  (procesos/máquinas independientes, Redis + misma BD)
```

## 3. Módulos y ubicación en el código

| # | Módulo | Paquete | Responsabilidad |
|---|---|---|---|
| 1 | Platform Connectors | `clipper/connectors/` | Un adaptador por plataforma (`youtube.py`, `twitch.py`, `kick.py`, `local.py`, `vod.py`, `ingest.py`). Detectan live, metadatos, resuelven la URL de medios y crean el cliente de chat. |
| 2 | Stream Capture | `clipper/capture/` | `hls.py`: descargador HLS propio con deduplicación por número de secuencia y `PROGRAM-DATE-TIME`, filtro de anuncios de Twitch, soporte `EXT-X-MAP` (fMP4). `ffmpeg_segmenter.py`: fuentes no HLS (archivos, VOD, RTMP/SRT). |
| 3 | Rolling Buffer | `clipper/buffer/rolling.py` | Índice de segmentos en disco por tiempo de stream, expulsión por antigüedad, *pins* para que un momento pendiente no pierda sus segmentos. |
| 4 | Audio Processing | `clipper/audio/` | Decodificación PyAV → PCM 16 kHz mono; rasgos por trama de 100 ms (RMS, pitch, ZCR, flatness, centroid, modulación). |
| 5 | Speech Recognition | `clipper/asr/` | `StreamingTranscriber`: corta en silencios (VAD), transcribe con faster-whisper con timestamps por palabra, prompt de continuidad, limpieza de alucinaciones; turnos de hablante ligeros. |
| 6 | Content Classifier | `clipper/analysis/classifier.py` | Categoría principal + secundarias con suavizado temporal; usa metadatos de la plataforma, léxico, rasgos visuales y LLM opcional. Se reevalúa continuamente. |
| 7 | Context Analyzer | `clipper/analysis/context.py` | Responde las 8 preguntas de contexto (qué pasa, quién habla, por qué interesa, inicio/desenlace, ¿se entiende solo?, mejor inicio/fin) con LLM o heurística; valida contra la transcripción. |
| 8 | Emotion Detector | `clipper/analysis/emotion.py` | Activación, risas, gritos, sorpresa, enfado, silencios relevantes; siempre busca la *causa* previa. |
| 9 | Story Detector | `clipper/analysis/story.py` | Arco narrativo (introducción → desarrollo → conflicto → clímax → desenlace) y sus límites. |
| 10 | Hook Detector | `clipper/analysis/hook.py` | Frases literales con potencial de gancho (curiosidad, sorpresa, conflicto, revelación, pregunta…) y si necesitan contexto previo. |
| 11 | Chat Analyzer | `clipper/analysis/chat.py` | Ráfagas (z-score vs. línea base), repetición, emotes→emoción, preguntas repetidas, alineación chat↔stream. |
| 12 | Visual Analyzer | `clipper/analysis/visual.py` | Cambios de escena, movimiento, caras, interacción entre personas, saliencia; se adapta a si hay o no caras. |
| 13 | Universal Highlight Engine | `clipper/analysis/engine.py` | Combina señales en tiempo real, abre candidatos, espera post-roll, evalúa, deduplica. |
| 14 | Highlight Ranker | `clipper/analysis/ranker.py` | 7 criterios (25/20/15/15/10/10/5), pesos adaptados al contexto + ajuste aprendido, niveles de prioridad configurables. |
| 15 | Clip Generator | `clipper/clips/` | Extracción del original (copy), límites naturales (sin cortar palabras), render H.264/AAC 1080×1920. |
| 16 | Caption Engine | `clipper/captions/` | Generador ASS (libass) con 7 estilos, animaciones, colores, posición, edición manual. |
| 17 | Smart Reframing | `clipper/reframe/` | Detección de caras/sujetos, hablante activo, trayectoria suavizada, modos track / split / fit / center / manual. |
| 18 | Video Editor | `clipper/editor/` | Modelo de edición (recorte, palabras, estilo, encuadre, título, marca de agua) → plan de render. |
| 19 | Training Library | `clipper/training/` | Importar clips externos propios, etiquetado, extracción de rasgos (duración, gancho, ritmo, narrativa, emociones, edición). |
| 20 | Feedback Learning | `clipper/learning/` | Dataset de feedback, modelo ligero con preferencias globales/creador/categoría, validación y versionado, preferencias de duración. |
| 21 | Social Performance Analyzer | `clipper/social/` | Publicaciones y métricas (manual, CSV, YouTube Analytics), insights, separación calidad editorial vs. rendimiento. |
| 22 | Dashboard API | `clipper/api/` | Routers REST + WebSocket de eventos. |
| 23 | Storage Manager | `clipper/storage/` | Rutas, cuotas, limpieza de temporales, retención. |

Infraestructura transversal: `clipper/core/` (config, logging, EventBus, JobQueue, cifrado de
credenciales, métricas del sistema) y `clipper/db/` (SQLAlchemy 2 async; SQLite o PostgreSQL).

## 4. Línea temporal y sincronización

* **Tiempo de stream** (`t`, segundos desde el inicio de la sesión) es la referencia común.
* Para HLS: `t` del segmento = `PDT(segmento) − PDT(primer segmento)` si hay
  `PROGRAM-DATE-TIME`; si no, suma acumulada de `EXTINF`. Huecos por reconexión se
  conservan (no se "comprime" el tiempo).
* Audio: el PCM de cada segmento se decodifica por separado y se coloca en `t_segmento`,
  así el reloj de audio no deriva aunque haya discontinuidades.
* Chat: llega con hora de pared. Se convierte con `t = (hora_msg − retraso_reacción) −
  hora_pared_inicio`, donde `hora_pared_inicio` sale del PDT (hora del emisor). El retraso
  de reacción es configurable (por defecto 6 s).

## 5. Detección en tiempo real

```
cada segmento ──► rasgos de audio, ASR (asíncrono), visión, chat
cada 2 s      ──► HighlightEngine.tick(now)
                   1. señal rápida s(t) = f(activación, eventos de audio, ráfaga de chat,
                      cambio visual, saliencia léxica de lo último transcrito)
                   2. si s(t) supera el umbral (según sensibilidad) → candidato
                   3. cada `scan_interval` (40 s) → candidato "semántico" sobre la ventana
                      reciente aunque no haya picos (educación, podcasts tranquilos)
                   4. candidato pendiente: pin de segmentos desde t−pre
                   5. cuando now ≥ t+post y el ASR alcanzó t+post → evaluar
```

Evaluación de un candidato (`MomentEvaluator`):
1. Palabras de `[t−pre_max, t+post]`, frases, turnos.
2. `StoryDetector` y `HookDetector` proponen límites; `EmotionDetector` identifica la
   reacción y su causa (frases anteriores).
3. `ContextAnalyzer` (LLM si está configurado) responde las 8 preguntas; su salida se valida
   (citas literales, límites dentro de la ventana).
4. Ajuste a límites naturales: inicio en comienzo de frase con silencio previo, fin tras
   cierre de frase/reacción; nunca dentro de una palabra; duración dentro de
   [min, max] y, si existe, de la preferencia aprendida del creador/categoría.
5. `HighlightRanker` → puntuación total, subpuntuaciones, confianza, nivel de prioridad.
6. Deduplicación: IoU temporal > 0,5 o similitud de transcripción > 0,6 con un momento
   reciente → se conserva el mejor.
7. Persistencia + extracción del original (stream copy de los segmentos con ±10 s de margen
   para poder ampliar el recorte en el editor) + liberación del pin.

## 6. Modos

* **Autopilot**: momento con puntuación ≥ `min_score` → render automático con el estilo por
  defecto del canal.
* **Review**: el momento queda en la cola "Clips detectados" con su original extraído; el
  render se lanza al aprobar (con o sin ediciones).
El modo se guarda por canal y se puede cambiar en caliente.

## 7. Render

`ClipGenerator.render(edit)`:
1. Plan de reencuadre (`reframe/planner.py`) sobre el original: análisis de caras a 5 fps,
   pistas, hablante activo (movimiento de boca × energía), trayectoria suavizada con zona
   muerta, cortes duros en cambios de hablante, o layout split/fit.
2. ASS de captions (`captions/ass.py`) y título.
3. Un único proceso FFmpeg: `trim → crop dinámico (sendcmd) | split | fit → scale 1080×1920
   → ass → marca de agua → libx264 (CRF 18, preset medium, high profile, yuv420p) / NVENC
   si hay CUDA → AAC 192 kbps 48 kHz → +faststart`.
4. Miniatura y metadatos. Eventos de progreso por WebSocket.

## 8. Concurrencia y recursos

| Recurso | Mecanismo |
|---|---|
| Descarga HLS | tarea asyncio por stream (httpx) |
| Decodificación + rasgos | `ThreadPoolExecutor` compartido (PyAV/numpy liberan el GIL) |
| ASR | pool dedicado (1 modelo compartido, `asr_workers` hilos) con cola acotada por stream; si se acumula retraso se avisa en el dashboard |
| Visión | muestreo adaptativo (2 fps → 0,5 fps bajo carga) |
| Render | `render_concurrency` procesos FFmpeg (`nice`), cola persistente en BD; con Redis, workers externos |
| Disco | buffer circular por sesión + verificación de espacio libre mínimo; limpieza periódica |

## 9. Persistencia

SQLite (WAL) por defecto; PostgreSQL con `CLIPPER_DATABASE_URL=postgresql+asyncpg://…`.
Tablas: `channels`, `stream_sessions`, `moments`, `clips`, `feedback`, `training_examples`,
`model_versions`, `publications`, `metric_snapshots`, `credentials`, `settings`, `jobs`.
Los trabajos de render se guardan en `jobs` y se reanudan tras un reinicio; los canales con
monitorización activa se reanudan al arrancar.

## 10. Seguridad y cumplimiento

* Credenciales (tokens OAuth, claves API) cifradas con Fernet (`CLIPPER_SECRET_KEY`); la API
  nunca las devuelve.
* API ligada a `127.0.0.1` por defecto; token de API opcional (`CLIPPER_API_TOKEN`) para
  REST y WebSocket.
* Webhooks de Kick verificados con RSA-SHA256 sobre `message_id.timestamp.body` y la clave
  pública oficial; ventana anti-replay de 5 min.
* No se graban transmisiones completas: solo el buffer circular temporal (se borra al
  expulsar y al terminar la sesión) y los fragmentos seleccionados. Al añadir un canal el
  usuario debe confirmar que tiene permiso del creador.
* Los clips de la Training Library son referencias de calidad privadas; nunca se republican.
