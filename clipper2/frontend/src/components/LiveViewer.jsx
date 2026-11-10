import { useEffect, useRef, useState } from 'react';
import { Radio, Scissors, Square, ArrowLeft, Download, Loader2, AlertTriangle } from 'lucide-react';
import { useAppStore } from '../store/useAppStore';

function buildEmbed(raw) {
  const url = (raw || '').trim();
  if (!url) return null;
  let u;
  try { u = new URL(/^https?:\/\//i.test(url) ? url : 'https://' + url); } catch { return null; }
  const host = u.hostname.replace(/^www\./, '').replace(/^m\./, '');
  const pq = ['localhost', '127.0.0.1'].map((p) => `parent=${p}`).join('&');
  const seg = u.pathname.split('/').filter(Boolean);

  if (host.endsWith('youtube.com') || host === 'youtu.be') {
    let id = '';
    if (host === 'youtu.be') id = seg[0] || '';
    else if (u.pathname.startsWith('/watch')) id = u.searchParams.get('v') || '';
    else if (seg[0] === 'live' || seg[0] === 'embed' || seg[0] === 'shorts') id = seg[1] || '';
    if (id) return { ok: true, src: `https://www.youtube.com/embed/${id}?autoplay=1` };
    return { ok: false, reason: 'Para un directo de YouTube por canal, pega el enlace del vídeo en vivo (youtube.com/watch?v=…).' };
  }
  if (host.endsWith('twitch.tv')) {
    if (seg[0] === 'videos' && seg[1]) return { ok: true, src: `https://player.twitch.tv/?video=${seg[1]}&${pq}&autoplay=true` };
    if (seg.length >= 1) return { ok: true, src: `https://player.twitch.tv/?channel=${seg[0]}&${pq}&autoplay=true` };
  }
  if (host.endsWith('kick.com')) {
    if (seg.length >= 1 && seg[0] !== 'video') return { ok: true, src: `https://player.kick.com/${seg[0]}` };
  }
  return { ok: false, reason: 'No se puede incrustar este enlace, pero igual puedo grabarlo y cortar.' };
}

async function jpost(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || data?.message || `Error ${res.status}`);
  return data;
}

export default function LiveViewer() {
  const { dispatch } = useAppStore();
  const [url, setUrl] = useState('');
  const [embed, setEmbed] = useState(null);
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [recorded, setRecorded] = useState(0);
  const [clips, setClips] = useState([]);
  const [cutSeconds, setCutSeconds] = useState(() => localStorage.getItem('live_cut_seconds') || '45');
  const [cutting, setCutting] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const start = async () => {
    setError('');
    const em = buildEmbed(url);
    setEmbed(em);
    setStarting(true);
    try {
      await jpost('/api/v1/live/start', { url: url.trim() });
      setActive(true);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const s = await (await fetch('/api/v1/live/status')).json();
          setActive(Boolean(s.active));
          setRecorded(s.recorded_seconds || 0);
          setClips(s.clips || []);
          if (!s.active && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        } catch { /* ignore */ }
      }, 2000);
    } catch (err) {
      setError(err?.message || 'No se pudo iniciar el directo');
    } finally {
      setStarting(false);
    }
  };

  const cutNow = async () => {
    setError(''); setCutting(true);
    try {
      const info = await jpost('/api/v1/live/clip', { seconds: Number(cutSeconds) || 45 });
      setClips((prev) => [info, ...prev]);
    } catch (err) {
      setError(err?.message || 'No se pudo cortar');
    } finally {
      setCutting(false);
    }
  };

  const stop = async () => {
    try { await jpost('/api/v1/live/stop', {}); } catch { /* ignore */ }
    setActive(false);
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const fmt = (s) => {
    const n = Math.max(0, Math.round(s || 0));
    const m = Math.floor(n / 60);
    return `${m}:${String(n % 60).padStart(2, '0')}`;
  };

  return (
    <div className="min-h-full w-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex w-full max-w-[1380px] flex-col gap-4">

        <section className="surface-panel px-6 py-5 md:px-8">
          <button onClick={() => dispatch({ type: 'SET_PHASE', payload: 'import' })}
            className="mb-3 inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary">
            <ArrowLeft size={14} /> Volver
          </button>
          <div className="flex items-center gap-2.5">
            <div className="h-1.5 w-1.5 rounded-full bg-danger" />
            <span className="signal-label">En vivo</span>
          </div>
          <h1 className="signal-heading mt-2 text-3xl md:text-4xl">Mira el directo y <span className="text-accent">corta al instante</span></h1>
          <p className="mt-3 max-w-xl text-sm leading-7 text-text-secondary">
            Pega un directo de YouTube, Twitch o Kick. Lo ves aquí mientras se graba en segundo plano, y con <b>Cortar ahora</b> guardas los últimos segundos sin esperar a que termine.
          </p>
        </section>

        <section className="surface-panel p-5 md:p-6">
          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <input
              className="bento-input flex-1 font-mono text-sm"
              placeholder="Pega el directo (youtube.com/watch?v=… · twitch.tv/canal · kick.com/canal)"
              value={url} onChange={(e) => setUrl(e.target.value)} disabled={active}
            />
            {!active ? (
              <button onClick={start} disabled={!url.trim() || starting} className="btn-warm rounded-xl px-6 py-3 text-sm">
                {starting ? <Loader2 size={15} className="animate-spin" /> : <Radio size={15} />} Iniciar directo
              </button>
            ) : (
              <button onClick={stop} className="btn-secondary rounded-xl px-6 py-3 text-sm">
                <Square size={15} /> Detener
              </button>
            )}
          </div>
          {error && (
            <div className="mt-3 flex items-center gap-2 rounded-xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger">
              <AlertTriangle size={15} /> {error}
            </div>
          )}
        </section>

        <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
          <section className="surface-panel overflow-hidden p-0">
            <div className="aspect-video w-full bg-black">
              {embed && embed.ok ? (
                <iframe title="directo" src={embed.src} className="h-full w-full" allow="autoplay; fullscreen; encrypted-media" allowFullScreen />
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-text-muted">
                  <Radio size={30} strokeWidth={1.2} />
                  <p className="text-sm">{embed && !embed.ok ? embed.reason : 'Pega un directo y pulsa Iniciar para verlo aquí.'}</p>
                </div>
              )}
            </div>
            {active && (
              <div className="flex flex-wrap items-center gap-3 border-t border-[var(--color-border)] bg-black/30 px-4 py-3">
                <span className="inline-flex items-center gap-1.5 text-xs text-danger"><span className="live-dot h-2 w-2 rounded-full bg-danger" /> Grabando</span>
                <span className="font-mono text-xs text-text-muted">buffer {fmt(recorded)}</span>
                <div className="ml-auto flex items-center gap-2">
                  <span className="text-xs text-text-muted">últimos</span>
                  <input type="number" min={10} max={300} value={cutSeconds}
                    onChange={(e) => { setCutSeconds(e.target.value); localStorage.setItem('live_cut_seconds', e.target.value); }}
                    className="bento-input w-16 font-mono" />
                  <span className="text-xs text-text-muted">s</span>
                  <button onClick={cutNow} disabled={cutting || recorded < 5} className="btn-warm rounded-xl px-4 py-2.5 text-sm">
                    {cutting ? <Loader2 size={15} className="animate-spin" /> : <Scissors size={15} />} Cortar ahora
                  </button>
                </div>
              </div>
            )}
          </section>

          <section className="surface-panel p-4">
            <div className="section-eyebrow mb-3">Cortes ({clips.length})</div>
            {clips.length === 0 ? (
              <div className="rounded-xl border border-[var(--color-border)] bg-black/20 px-4 py-10 text-center text-sm text-text-muted">
                Aún no hay cortes. Pulsa <b>Cortar ahora</b> cuando pase algo bueno.
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {clips.map((c) => (
                  <div key={c.name} className="rounded-xl border border-[var(--color-border)] bg-black/20 p-2">
                    <video src={c.url} controls className="w-full rounded-lg bg-black" />
                    <div className="mt-2 flex items-center justify-between px-1 text-xs text-text-muted">
                      <span className="font-mono">{c.name} · {c.duration}s</span>
                      <a href={c.url} download className="inline-flex items-center gap-1 text-accent hover:underline">
                        <Download size={13} /> Descargar
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

      </div>
    </div>
  );
}
