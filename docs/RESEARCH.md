# Investigación previa: proyectos, licencias, componentes y modelos

Fecha: noviembre 2026. Las licencias se verificaron con la API de búsqueda de GitHub
(`license.spdx_id`). Los datos de las APIs de Kick se comprobaron con peticiones
reales a `api.kick.com` e `id.kick.com` desde el entorno de desarrollo.

## 1. Proyectos open source estudiados

| Proyecto | Licencia | Enfoque | Qué aprendimos / reutilizamos |
|---|---|---|---|
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | MIT | VOD → clips; segmentación por temas (TextTiling) + resize con caras y diarización | Idea de dividir el reencuadre por escenas y fijar un recorte por escena. Sin actividad desde 2024. No se copia código. |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | MIT | ASR (FunASR) + LLM para elegir fragmentos, VOD | Patrón "transcripción con timestamps → LLM elige rangos → validación contra la transcripción". |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | MIT | Local-first: Whisper + Ollama, clips 9:16 con captions y seguimiento del hablante | Confirma la viabilidad de Ollama local para puntuar momentos. VOD solamente. |
| [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) | **AGPL-3.0** | Alternativa a OpusClip | **No se reutiliza código** (AGPL obligaría a licenciar todo el proyecto igual). Solo referencia funcional. |
| Blueturboguy07/publikclip, osbytes/reelsaw, m-hoseyny/teek | **AGPL-3.0** | Clippers VOD con reencuadre | Mismo criterio: sin copiar código. |
| sqrtminustwo/twitch_auto_clipper | MIT | Clips de Twitch cuando el chat se dispara | Confirma que la mayoría de clippers "en vivo" usan solo el chat: nosotros lo usamos como señal complementaria. |
| VadlapatiKarthik/autoclipper | sin licencia | Clips de YouTube/Twitch/Kick | Sin licencia = todos los derechos reservados. No reutilizable. |
| [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming) | MIT | Whisper en streaming (política LocalAgreement) | Inspiración para el troceado por silencios y el prompt de continuidad entre fragmentos. |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | BSD-2 | Alineación forzada + diarización | Mejor precisión por palabra, pero exige PyTorch y modelos pyannote con acceso restringido. Queda como backend opcional futuro. |
| [jianfch/stable-ts](https://github.com/jianfch/stable-ts) | MIT | Estabilización de timestamps | Técnica de ajustar límites de palabras a la energía/silencio: la aplicamos en `clips/boundaries.py`. |
| [xenova/chat-downloader](https://github.com/xenova/chat-downloader) | MIT | Scraping de chats | No se usa: depende de endpoints no oficiales. |
| [TwitchIO/TwitchIO](https://github.com/TwitchIO/TwitchIO) | MIT | Librería completa de Twitch | Demasiado grande para lo que necesitamos (lectura de chat + Helix). Cliente propio de ~200 líneas. |

**Conclusión del análisis:** prácticamente todos los clippers open source son *VOD-first*
(procesan el vídeo cuando ya existe completo) y los pocos "en vivo" dependen solo del chat.
Ninguno combina captura continua con buffer circular, ASR incremental, análisis
multimodal y aprendizaje por feedback. Por eso la arquitectura es propia y solo se
reutilizan **librerías** con licencias permisivas, no proyectos completos.

## 2. Librerías reutilizadas (dependencias, no código copiado)

| Componente | Librería | Licencia | Uso |
|---|---|---|---|
| Resolución de streams | [streamlink](https://github.com/streamlink/streamlink) | BSD-2-Clause | Obtener la playlist HLS de Twitch, Kick y YouTube (plugins mantenidos por la comunidad). |
| Resolución YouTube (fallback) | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | Manifiesto HLS de directos de YouTube si streamlink falla. |
| ASR | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Transcripción con timestamps por palabra, int8 en CPU, float16 en CUDA. |
| VAD | [silero-vad](https://github.com/snakers4/silero-vad) (incluido en faster-whisper) | MIT | Detección de voz/silencio. |
| Decodificación | [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | Decodificar audio y fotogramas de cada segmento sin lanzar procesos. |
| Render | FFmpeg (libx264, libass, AAC) | LGPL/GPL (binario del sistema) | Extracción, reencuadre, captions ASS, codificación. Se invoca como proceso externo. |
| Visión | [OpenCV](https://github.com/opencv/opencv) + [YuNet](https://github.com/opencv/opencv_zoo) | Apache-2.0 / MIT | Detección de caras (YuNet; Haar cascade como respaldo sin descarga), movimiento, saliencia. |
| Aprendizaje | scikit-learn | BSD-3 | Regresión logística ligera, validación cruzada, AUC. |
| Tipografías | [Fontsource](https://github.com/fontsource/fontsource) (Montserrat, Anton, Bebas Neue, Poppins, Inter, Bangers) | Fuentes OFL-1.1 | Captions. Se convierten de WOFF2 a TTF para libass. |
| LLM local | [Ollama](https://github.com/ollama/ollama) | MIT | Comprensión contextual y clasificación sin enviar datos fuera. |

## 3. Comparación de modelos de IA

### Reconocimiento de voz
| Opción | Ventajas | Inconvenientes | Decisión |
|---|---|---|---|
| faster-whisper | 4x más rápido que Whisper original, int8 en CPU, timestamps por palabra, VAD integrado | Timestamps por palabra menos precisos que la alineación forzada | **Elegido** (backend por defecto) |
| openai-whisper | Referencia | PyTorch, más lento | Compatible vía misma interfaz si se desea |
| WhisperX | Palabras muy precisas, diarización | PyTorch + modelos con acceso restringido | Futuro backend opcional |

Tamaño recomendado: CPU de 4 núcleos → `small` int8 (≈tiempo real) o `base` para varios
streams; GPU NVIDIA → `large-v3-turbo` float16.

### Eventos de audio (risas, gritos, aplausos)
| Opción | Licencia | Notas | Decisión |
|---|---|---|---|
| Rasgos DSP (energía, pitch, ZCR, flatness, modulación 3–7 Hz) | propio | Sin dependencias, tiempo real | **v1** |
| PANNs CNN14 | MIT | 527 clases AudioSet, requiere PyTorch | Plugin opcional |
| AST (AudioSet) | BSD-3 | Más preciso, más pesado | Plugin opcional |
| YAMNet | Apache-2.0 | TensorFlow | Descartado (otra pila de ML) |

### Caras / sujetos
| Opción | Licencia | Notas | Decisión |
|---|---|---|---|
| YuNet (OpenCV DNN) | MIT | 230 KB, rápido en CPU, puntos faciales | **Elegido** |
| Haar cascade (incluido en OpenCV) | BSD | Sin descarga, menos preciso | Respaldo |
| MediaPipe | Apache-2.0 | Bueno, pero las ruedas para Python 3.13 no están garantizadas | Descartado en v1 |
| InsightFace / RetinaFace | modelos no comerciales | Licencia de modelos restrictiva | Descartado |

Cuando no hay caras (gameplay, cocina, deportes, paisajes IRL) se usa saliencia espectral
residual + movimiento para estimar la región de interés, y el modo *fit* (fondo
difuminado) cuando el recorte eliminaría información.

### Comprensión contextual (LLM)
| Opción | Notas |
|---|---|
| Ollama local (`qwen2.5:7b-instruct`, `llama3.1:8b`; visión: `qwen2.5vl`, `llama3.2-vision`) | Privado, sin coste, ~1–4 s por ventana en GPU |
| Endpoint compatible con OpenAI (vLLM, LM Studio, llama.cpp server, servicios externos) | Mismo cliente |
| Anthropic Messages API | Opcional, configurable |
| Sin LLM | Motor heurístico multiseñal (siempre disponible) |

El LLM **nunca** puede inventar texto: el gancho y los límites que devuelve se validan
contra la transcripción real (`analysis/context.py`).

### Aprendizaje por feedback
Regresión logística regularizada con *feature augmentation* (copias global / por creador /
por categoría, técnica de Daumé III 2007): un único modelo ligero aprende preferencias
generales y específicas. Se valida con validación cruzada (AUC, log-loss) contra el modelo
en producción antes de promoverlo.
