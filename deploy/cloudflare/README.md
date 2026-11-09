# Publicar Live Clipper en tu subdominio con Cloudflare Tunnel (gratis)

Tu PC sigue haciendo el trabajo, pero queda accesible en `https://clipper.tudominio` con HTTPS,
sin abrir puertos ni alquilar un servidor. Esto también **desbloquea publicar en Instagram** (ya hay
URL pública). Coste: 0 €/mes (además del dominio que ya tienes en Porkbun).

## 1. Pon tu dominio en Cloudflare (una vez, ~10 min)

Cloudflare Tunnel con dominio propio necesita que el DNS del dominio lo gestione Cloudflare. Sigues
con el dominio **registrado en Porkbun**, solo cambias los *nameservers*:

1. Crea una cuenta gratis en https://dash.cloudflare.com y pulsa **Add a site** → escribe tu dominio → plan **Free**.
2. Cloudflare te dará **2 nameservers** (p. ej. `xxx.ns.cloudflare.com`).
3. En Porkbun: tu dominio → **Authoritative Nameservers** → **Edit** → sustituye por los de Cloudflare → Guarda.
4. Espera a que Cloudflare marque el dominio como **Active** (suele ser minutos, a veces horas).

## 2. Crea el túnel en Cloudflare (una vez)

1. En el panel de Cloudflare: **Zero Trust** (menú lateral) → la primera vez te pide crear un equipo (nombre libre, plan **Free**).
2. **Networks → Tunnels → Create a tunnel** → tipo **Cloudflared** → ponle un nombre (p. ej. `live-clipper`) → **Save**.
3. En **Public Hostnames** → **Add a public hostname**:
   - **Subdomain:** `clipper`  ·  **Domain:** tu dominio
   - **Service:** Type `HTTP`, URL `localhost:8000`
   - **Save**.
4. Arriba, en **Install and run a connector**, copia el **token** del túnel (la cadena larga tras `--token`).

## 3. En tu PC

Ejecuta **`windows/LiveClipper.bat`** (doble clic). Arranca la app y, al final, te pregunta si levantar el túnel; responde que sí y te pedirá:
- la **URL pública** (p. ej. `https://clipper.tudominio.com`),
- el **token** del túnel que copiaste.

El script descarga `cloudflared` en versión portable (sin instalar nada ni administrador), guarda la URL pública en tu configuración, genera un **token
de acceso** para que solo entres tú, arranca Live Clipper y levanta el túnel. Deja esa ventana abierta.

La primera vez, al abrir `https://clipper.tudominio` la app te pedirá ese token de acceso una vez
(aparece en la ventana del script y en `backend\.env` como `CLIPPER_API_TOKEN`). Se guarda en el navegador.

## 4. Si vas a publicar en redes

Cuando la app está en tu subdominio, las redirecciones de OAuth y los webhooks usan esa URL. En cada
app de desarrollador añade como redirección autorizada:

```
https://clipper.tudominio.com/api/oauth/google/callback
https://clipper.tudominio.com/api/oauth/tiktok/callback
https://clipper.tudominio.com/api/oauth/meta/callback
```

Y en Kick, la URL de webhooks será `https://clipper.tudominio.com/api/webhooks/kick`.

## Notas

- La app escucha solo en `localhost`; Cloudflare se conecta a ella desde tu propio PC. No se abren puertos.
- Tu PC debe estar encendido para que el subdominio responda.
- El token de acceso protege `/api`; las rutas de OAuth y webhooks quedan públicas a propósito (las plataformas tienen que poder llamarlas; los webhooks de Kick van firmados).
- Para apagar: cierra la ventana del script.
