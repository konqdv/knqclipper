# clipper2 — fork de Stream-Clipper-Factory + conectores multiplataforma

Esta carpeta parte de **[Stream-Clipper-Factory](https://github.com/Cbhhhh211/Stream-Clipper-Factory)**
de **Cbhhhh211**, bajo licencia **MIT** (ver `LICENSE`, que se conserva íntegra). Todo el crédito del
código base original es suyo.

## Por qué este fork
Stream-Clipper-Factory es local, gratuito, con una interfaz excelente y **nuestro mismo stack**
(React + Vite + Tailwind + FastAPI + faster-whisper), pero está orientado a YouTube y plataformas
chinas (Bilibili/Douyin). Le añadimos lo que necesitábamos y que ya habíamos construido en el proyecto
anterior (`backend/` de este repo):

## Qué añadimos (plan)
1. **Conectores multiplataforma** en `stream_clipper/ingest/`:
   - **Twitch** (directo + VOD + clips) y **Kick** (directo + VOD) — vía yt-dlp (ya soportado) + captura de directo.
   - **YouTube Live** (captura en tiempo real), además del YouTube/VOD que ya trae.
2. **Chat en vivo → señal de "danmaku"**: Twitch IRC, Kick y YouTube live-chat, para alimentar la
   detección de momentos (que ya usa densidad de chat). Se porta desde `backend/clipper/connectors/`.
3. **Publicación en redes** (YouTube Shorts, TikTok, Instagram Reels/Feed, Facebook Reels) — se porta
   desde `backend/clipper/publish/`.
4. **Generador de textos virales/SEO** por red — se porta desde `backend/clipper/analysis` / `publish/copy`.
5. **Editor** (recorte, subtítulos animados, reencuadre) según haga falta.

## Estado
- [x] Base vendorizada (código original intacto + esta nota).
- [ ] Conectores Twitch / Kick / YouTube-Live.
- [ ] Chat multiplataforma.
- [ ] Publicación en redes.
- [ ] Generador SEO.

> Nota: lo ideal a futuro es un **fork propio en GitHub** (`Fork` en el repo original) para mantener el
> linaje y poder traer actualizaciones de upstream. Esto se vendorizó en una carpeta porque esta sesión
> no puede crear repositorios nuevos. Migrar a un fork propio es sencillo cuando quieras.
