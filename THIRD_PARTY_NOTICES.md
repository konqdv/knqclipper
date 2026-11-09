# Third-party notices

This project depends on the libraries below (installed as packages, not copied into the source tree)
and bundles the fonts listed at the end.

| Component | License | Use |
|---|---|---|
| FastAPI, Starlette, Pydantic, Uvicorn | MIT / BSD-3 | API server |
| SQLAlchemy, aiosqlite | MIT | persistence |
| httpx, websockets | BSD-3 | platform APIs, chat |
| streamlink | BSD-2-Clause | resolving live HLS playlists |
| yt-dlp | Unlicense | YouTube playlist resolution fallback |
| faster-whisper, CTranslate2 | MIT | speech recognition |
| Silero VAD (via faster-whisper) | MIT | voice activity detection |
| PyAV | BSD-3-Clause | in-process decoding |
| OpenCV (opencv-python-headless) | Apache-2.0 | vision |
| YuNet face detector (opencv_zoo, downloaded at runtime) | MIT | face detection |
| NumPy, scikit-learn | BSD-3 | features and learning |
| cryptography | Apache-2.0 / BSD | credential encryption, webhook signatures |
| psutil | BSD-3 | resource metrics |
| FFmpeg (system binary, invoked as a process) | LGPL/GPL depending on build | capture, extraction, rendering |
| React, React Router, TanStack Query | MIT | UI |
| Tailwind CSS, Vite | MIT | UI build |
| Recharts | MIT | charts |
| lucide-react | ISC | icons |

## Bundled fonts (`backend/clipper/assets/fonts/`)

Montserrat, Anton, Bebas Neue, Poppins, Inter and Bangers are licensed under the SIL Open Font
License 1.1 (license texts next to the font files). They were obtained from the Fontsource npm
packages and converted from WOFF2 to TTF (with the family name set per weight) by
`backend/scripts/fetch_fonts.py` so that libass can load them.

## Models downloaded at runtime (not redistributed)

- Whisper models converted for CTranslate2 (`Systran/faster-whisper-*`, MIT).
- `opencv/face_detection_yunet` (MIT).
