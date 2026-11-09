# Universal AI Live Clipper

Plataforma que **observa transmisiones en vivo** de YouTube, Twitch y Kick (además de archivos,
VOD e ingesta RTMP/SRT), entiende lo que está pasando, detecta los momentos con potencial
**mientras el directo sigue activo** y genera clips verticales 1080×1920 con subtítulos animados,
listos para TikTok, Reels y Shorts. Aprende de tus aprobaciones, rechazos y del rendimiento real
de las publicaciones.

No está especializada en gaming: el clasificador universal identifica el tipo de contenido
(podcast, debate, IRL, educación, noticias, deportes, cocina, música, gaming…) y **solo reajusta
pesos**; todas las señales se evalúan siempre.

```
Directo ─► captura HLS/FFmpeg ─► buffer circular ─► audio · ASR · visión · chat
                                                   ─► motor universal (disparadores + escaneo semántico)
                                                   ─► historia · gancho · contexto (LLM opcional) · ranker
                                                   ─► original extraído ─► Review / Autopilot ─► render 9:16
```

Documentación técnica:
[arquitectura](docs/ARCHITECTURE.md) · [plataformas y limitaciones](docs/PLATFORMS.md) ·
[investigación y licencias](docs/RESEARCH.md) · [plan por fases](docs/IMPLEMENTATION_PLAN.md)

## Requisitos

- Python ≥ 3.11 (probado con 3.13), FFmpeg ≥ 6 con `libx264`, `libass`, `libfreetype` (el de
  Debian/Ubuntu sirve).
- Node ≥ 18 solo para compilar la interfaz.
- Opcional: GPU NVIDIA con CUDA (ASR float16 + NVENC), Redis (workers independientes),
  PostgreSQL, Ollama u otro proveedor LLM.

## Windows: instalación con un clic

1. Descarga [`windows/Instalar-LiveClipper.bat`](windows/Instalar-LiveClipper.bat) (botón «Download raw file»).
2. Haz doble clic. Si Windows muestra «Windows protegió tu PC», pulsa **Más información → Ejecutar de todas formas**.
3. El instalador:
   - instala lo que falte con `winget` (Git, Python 3.12, Node.js LTS y FFmpeg);
   - descarga el código en `%USERPROFILE%\knqclipper`;
   - compila la interfaz e instala las dependencias;
   - descarga el modelo de voz;
   - crea el acceso directo **Live Clipper** en el escritorio.
4. Al terminar se abre `http://localhost:8000`. Las siguientes veces basta con el acceso directo; para apagar la app, cierra su ventana.

Volver a ejecutar el instalador actualiza el código. El registro queda en `%TEMP%\knqclipper-install.log`.

## Instalación y arranque

```bash
# backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[redis,dev]"
cp .env.example .env            # opcional: credenciales, modelo de Whisper, LLM…
clipper serve                   # http://127.0.0.1:8000

# interfaz (en otra terminal)
cd frontend
npm install
npm run build                   # el backend sirve frontend/dist en http://127.0.0.1:8000
# o, para desarrollo con recarga: CLIPPER_API=http://127.0.0.1:8000 npm run dev  → http://localhost:5173
```

El primer directo descarga el modelo de Whisper configurado (`CLIPPER_WHISPER_MODEL`, por defecto
`small`) y el detector de caras YuNet desde HuggingFace.

### Workers independientes (Redis)

```bash
CLIPPER_QUEUE_BACKEND=redis CLIPPER_RUN_LOCAL_RENDER_WORKERS=false clipper serve
CLIPPER_QUEUE_BACKEND=redis clipper worker --types render training_analyze --concurrency 2   # en 1..N procesos/máquinas
```

Los trabajos se guardan en la base de datos (se reanudan tras un reinicio) y los eventos de los
workers llegan a la interfaz por Redis pub/sub. Con varias máquinas, usa PostgreSQL y un
`CLIPPER_DATA_DIR` compartido.

## Uso

1. **Añadir transmisión**: pega un enlace (`twitch.tv/canal`, `youtube.com/@canal` o un vídeo,
   `kick.com/canal`, una ruta local, `rtmp://0.0.0.0:1935/live/clave`). La app consulta el estado
   real, muestra las capacidades y limitaciones de esa plataforma y pide confirmar el permiso
   del creador.
2. El supervisor detecta cuándo empieza el directo, captura segmentos, transcribe y analiza.
   **Live Streams** muestra señales, transcripción y categoría en vivo; un canal solo aparece como
   «Procesando en vivo» cuando llegan segmentos reales.
3. **Review** (por defecto): los candidatos aparecen en **Clips detectados** con puntuación,
   subpuntuaciones, motivo, contexto y transcripción; apruébalos, recházalos con motivos,
   califícalos de 1 a 5 o ábrelos en el **Editor**. **Autopilot**: se generan los clips con
   puntuación ≥ mínima. El modo se cambia en caliente.
4. **Editor**: recorte, transcripción y palabras destacadas, 7 estilos de captions, tipografía,
   colores, posición y animación, encuadre (automático, seguir hablante, dividido, completo,
   centro, manual), título inicial y marca de agua, con vista previa en tiempo real y
   previsualización renderizada.
5. **Publicar en redes**: en un clip renderizado, botón **Publicar** → genera títulos, descripciones y hashtags por red con «Generar textos» (IA o análisis del clip), elige YouTube Shorts (directo), TikTok (borrador), Instagram Reels/Feed y Facebook Reels, y edita el texto de cada red. Conecta las cuentas en Configuración → Publicar en redes. Instagram exige que la app esté en un servidor con URL pública.
5. **Training Library**: importa clips propios de referencia y califícalos; se analizan con el
   mismo pipeline. **Analytics** registra métricas de TikTok/Reels/Shorts (manual, CSV o YouTube
   Analytics) y muestra qué duraciones, ganchos, categorías y estilos rinden mejor.

## Estado del proyecto (esta iteración)

Todas las fases tienen implementación funcional. Esto es lo que se **verificó de verdad** en el
entorno de desarrollo y lo que no:

| Área | Estado | Evidencia |
|---|---|---|
| Pipeline en vivo completo | ✅ verificado | Archivo con dos voces a cadencia de directo → captura → ASR (RTF 0,13 con `base` en CPU, retraso ≈1–4 s) → momento detectado en vivo (inicio en la apertura de la historia, 76–79/100) → original extraído → clip 1080×1920 H.264 High/AAC 48 kHz con seguimiento del hablante y captions |
| Review y feedback desde la UI | ✅ verificado | Flujo Playwright: activar Review → monitorizar → aprobar con motivo y 4/5 → render |
| Workers Redis | ✅ verificado | El API encola; un `clipper worker` aparte renderiza; el API no ejecuta FFmpeg |
| Captura HLS | ✅ verificado contra servidor HLS local | Deduplicación por PDT/secuencia, salto de anuncios con hueco temporal conservado, re-resolución tras 403 |
| Twitch | ✅ estado y chat reales | Estado (sin credenciales, vía GraphQL público) con título/categoría/espectadores reales; chat IRC anónimo recibió mensajes reales |
| Kick | ✅ parcial | Clave pública de webhooks (RSA-2048) y endpoint `client_credentials` comprobados; estado por endpoint web |
| YouTube | ⚠️ parcial | Detección de directo vía yt-dlp funcionó una vez; después YouTube pidió verificación anti-bot a la IP del servidor (se informa como error, no como «offline»). Usa `CLIPPER_YOUTUBE_API_KEY` |
| Descarga de vídeo real de las plataformas | ❌ no verificable aquí | La red del entorno bloquea los CDN dinámicos (`*.ttvnw.net`, `*.googlevideo.com`, `*.live-video.net`). Ver [requisitos de red](docs/PLATFORMS.md#requisitos-de-red-del-despliegue) |
| APIs con credenciales (Helix, Kick oficial, YouTube Data/Analytics, Google OAuth) | ⚠️ implementadas, no probadas | No había credenciales; el código sigue la documentación oficial |
| LLM (compatible OpenAI) | ✅ verificado con modelo local | Qwen2.5-1.5B (GGUF) servido por llama.cpp: el pipeline en vivo analizó el momento con el LLM (qué sucede, por qué interesa, título), con el gancho validado como cita literal; índices inválidos y puntuaciones degeneradas (todo 1,0) se descartan. Ollama y Anthropic usan el mismo contrato pero no se probaron aquí |
| Aprendizaje | ✅ probado con datos sintéticos en tests | Preferencias por creador separadas (AUC CV > 0,8), validación antes de promover |
| Publicación en redes + generador de textos | ⚠️ implementado, no probado en vivo | Adaptadores YouTube/TikTok/Instagram/Facebook verificados con HTTP simulado; publicación real necesita tus apps + revisión de cada plataforma |
| Pruebas automatizadas | ✅ 60 tests | `cd backend && pytest` |

### Limitaciones conocidas y siguientes pasos

- Risas/gritos se detectan con rasgos DSP y se descartan si coinciden con habla transcrita; un
  clasificador de eventos AudioSet (PANNs/AST, requiere PyTorch) mejoraría la precisión.
- Turnos de hablante por timbre + tono (sin diarización neuronal); expresión facial aproximada
  con los puntos de YuNet.
- Chat oficial de Kick por webhooks exige `CLIPPER_PUBLIC_BASE_URL` accesible desde Internet.
  Twitch EventSub (`channel.chat.message`) requiere token de usuario: se usa IRC de solo lectura.
- TikTok e Instagram exigen apps aprobadas por las plataformas: sus métricas se importan a mano o
  por CSV.
- Sin migraciones Alembic todavía (el esquema se crea al arrancar). Usuario único con token de API
  opcional.
- Probado en CPU; NVENC/CUDA se activan automáticamente si existen, pero no se probaron aquí.

## Desarrollo

```bash
cd backend && pytest                    # 60 tests (los de ASR usan el modelo tiny si está en caché)
cd frontend && npx tsc -b && npm run build
```

Respeta los términos de cada plataforma y los derechos de los creadores: el sistema solo guarda el
buffer temporal (se borra al expulsarse y al terminar la sesión) y los fragmentos seleccionados.
