# Plataformas: capacidades, métodos de acceso y limitaciones

Las tres plataformas **no** ofrecen las mismas capacidades. Cada adaptador implementa solo lo
que su plataforma permite y declara sus capacidades (`ConnectorCapabilities`) para que la UI
muestre lo que realmente está disponible.

## Resumen

| Capacidad | YouTube Live | Twitch | Kick | Archivo local / VOD | Ingest RTMP/SRT |
|---|---|---|---|---|---|
| Detectar live (oficial) | Data API v3 (`videos.list` 1 u., `search.list` 100 u.) | Helix `GET /streams` (token de app) | `GET /public/v1/channels` (token de app) | n/a | conexión entrante |
| Detectar live sin credenciales | página `/live` vía streamlink/yt-dlp | streamlink | streamlink | n/a | n/a |
| Audio/vídeo | **No hay API oficial**: HLS vía streamlink / yt-dlp | HLS vía streamlink (mismo flujo que el reproductor web) | HLS (IVS) vía streamlink | archivo | push desde OBS |
| Chat (oficial) | `liveChatMessages.list` (5 u. por llamada) | IRC por WebSocket, lectura anónima | webhooks `chat.message.sent` (requiere URL pública) | — | — |
| Chat (no oficial, opcional) | — | — | WebSocket Pusher (desactivado por defecto) | — | — |
| Metadatos de categoría | `snippet.categoryId` | `game_name` | `category.name` | — | — |

## YouTube Live

* **Estado del directo**: con `CLIPPER_YOUTUBE_API_KEY`, `videos.list?part=liveStreamingDetails,snippet`
  (1 unidad) para un vídeo conocido; para un canal, `search.list?eventType=live&type=video`
  (100 unidades) — con la cuota diaria por defecto (10 000 u.) conviene un intervalo de
  sondeo ≥ 5 min por canal. Sin clave se usa la redirección `youtube.com/@canal/live`.
* **Medios**: la API de YouTube no expone el vídeo de terceros. El adaptador obtiene el
  manifiesto HLS del directo con streamlink (fallback yt-dlp). Úsalo solo con
  transmisiones propias o con permiso del creador. Alternativa 100 % oficial para el
  propio creador: **ingest RTMP** (OBS → salida adicional hacia el clipper).
* **Chat**: `liveChatMessages.list` respetando `pollingIntervalMillis`; cada llamada cuesta 5
  unidades, por lo que el sondeo continuo de un directo largo consume cuota rápido. Se
  puede desactivar el chat por canal.
* **Red**: los segmentos se sirven desde `*.googlevideo.com` (hosts dinámicos).

## Twitch

* **Estado**: Helix `GET https://api.twitch.tv/helix/streams?user_login=…` con token de app
  (`client_credentials`, `CLIPPER_TWITCH_CLIENT_ID/SECRET`). Devuelve `started_at`,
  `game_name`, `title`, `viewer_count`. Sin credenciales: streamlink.
* **Medios**: playlist HLS del reproductor (usher). El capturador propio descarta segmentos
  de anuncios insertados (título `Amazon` o `DATERANGE` `twitch-stitched-ad`) y soporta
  segmentos fMP4 (`EXT-X-MAP`). Twitch puede insertar anuncios: esos segundos no se
  analizan ni aparecen en clips.
* **Chat**: IRC sobre WebSocket (`wss://irc-ws.chat.twitch.tv`), login anónimo `justinfanNNNN`,
  solo lectura, con capacidad `twitch.tv/tags` (emotes, ids). EventSub
  `channel.chat.message` es la alternativa moderna pero requiere token de usuario.
* **Red**: segmentos en `*.ttvnw.net` (hosts dinámicos).

## Kick

* **API oficial** (`https://api.kick.com/public/v1`, OAuth 2.1 en `id.kick.com`):
  `client_credentials` disponible para token de app (verificado). `GET /channels?slug=`
  requiere token. Devuelve el estado del stream y la categoría.
* **Medios**: el reproductor usa AWS IVS (HLS, `*.live-video.net`); streamlink resuelve la
  playlist desde la API interna `kick.com/api/v2`, que **no es oficial** y puede cambiar o
  ser bloqueada por Cloudflare.
* **Chat oficial**: webhooks `chat.message.sent` mediante `POST /public/v1/events/subscriptions`.
  Requiere `CLIPPER_PUBLIC_BASE_URL` accesible desde Internet. Firma verificada con
  RSA-SHA256 y la clave de `GET /public/v1/public-key` (verificado: clave RSA 2048).
* **Chat no oficial (opcional)**: WebSocket Pusher; desactivado por defecto
  (`CLIPPER_KICK_UNOFFICIAL_CHAT=true` para habilitarlo bajo responsabilidad del usuario).

## Archivos locales y VOD

* Archivos: se procesan con el mismo pipeline. `realtime=true` simula la cadencia de un
  directo (útil para ensayar); `realtime=false` procesa a máxima velocidad.
* VOD/grabaciones de plataformas: URL resuelta por streamlink/yt-dlp; mismas restricciones
  de permisos que el directo.

## Ingest RTMP/SRT (alternativa legítima universal)

El creador añade una salida extra en OBS (o un servicio de multistream) apuntando a
`rtmp://<host>:1935/live/<clave>`. Es la vía recomendada para el propio canal porque no
depende de endpoints no documentados y tiene la mínima latencia.

## Requisitos de red del despliegue

Para captura real, la red debe permitir: `api.twitch.tv`, `id.twitch.tv`, `gql.twitch.tv`,
`usher.ttvnw.net`, **`*.ttvnw.net`**, `irc-ws.chat.twitch.tv`, `www.youtube.com`,
`www.googleapis.com`, **`*.googlevideo.com`**, `kick.com`, `api.kick.com`, `id.kick.com`,
**`*.live-video.net`**, `huggingface.co` (descarga de modelos Whisper).
Los hosts con comodín son obligatorios: las plataformas sirven los segmentos desde
servidores que cambian en cada sesión.
