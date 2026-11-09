import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Info, Search, Upload } from "lucide-react";
import { useState } from "react";
import { api, uploadWithProgress } from "../lib/api";
import type { Capabilities, Channel, LiveState } from "../lib/types";
import { Field, Modal, PlatformBadge, Spinner } from "./ui";

interface Resolved {
  source: { platform: Channel["platform"]; handle: string; kind: string; display_name: string | null; url: string };
  status: LiveState & { display_name?: string | null };
  capabilities: Capabilities;
}

export default function AddStreamModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [rights, setRights] = useState(false);
  const [cfg, setCfg] = useState({ mode: "review", min_score: 75, sensitivity: 0.5, buffer_seconds: 120, pre_seconds: 45, post_seconds: 12, chat_enabled: true, realtime: true });
  const resolve = useMutation({ mutationFn: (u: string) => api.post<Resolved>("/api/channels/resolve", { url: u }) });
  const [progress, setProgress] = useState<number | null>(null);
  const upload = useMutation({
    mutationFn: (f: File) => uploadWithProgress<{ path: string; name: string }>("/api/uploads", f, setProgress),
    onSuccess: (r) => {
      setProgress(null);
      setUrl(r.path);
      resolve.mutate(r.path);
    },
    onError: () => setProgress(null),
  });
  const add = useMutation({
    mutationFn: () => api.post<Channel>("/api/channels", { url, rights_confirmed: rights, start: true, config: cfg }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channels"] });
      setUrl("");
      setRights(false);
      resolve.reset();
      onClose();
    },
  });
  const r = resolve.data;
  const needsRights = r && !["local", "ingest"].includes(r.source.platform);

  return (
    <Modal open={open} onClose={onClose} title="Añadir transmisión" wide>
      <div className="space-y-5">
        <div className="flex gap-2">
          <input className="input" placeholder="https://twitch.tv/canal · https://youtube.com/@canal · https://kick.com/canal · /ruta/video.mp4 · rtmp://0.0.0.0:1935/live/clave"
            value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && url && resolve.mutate(url)} />
          <button className="btn" disabled={!url || resolve.isPending} onClick={() => resolve.mutate(url)}>
            {resolve.isPending ? <Spinner /> : <Search size={16} />} Comprobar
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-ink-3">
          <span>¿Sin directo a mano? Prueba con un vídeo tuyo:</span>
          <label className={`btn cursor-pointer ${upload.isPending ? "pointer-events-none opacity-60" : ""}`}>
            <Upload size={15} /> Subir vídeo
            <input type="file" accept="video/*,.mkv,.ts,.flv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          </label>
          {progress !== null && (
            <div className="flex min-w-48 flex-1 items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-series-1-track"><div className="h-full rounded-full bg-accent" style={{ width: `${progress * 100}%` }} /></div>
              <span className="tabular">{Math.round(progress * 100)}%</span>
            </div>
          )}
          <span>Se procesa con el mismo pipeline que un directo.</span>
        </div>
        {upload.error && <div className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-200 ring-1 ring-rose-500/30">{(upload.error as Error).message}</div>}
        {resolve.error && <div className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-200 ring-1 ring-rose-500/30">{(resolve.error as Error).message}</div>}

        {r && (
          <div className="grid gap-4 md:grid-cols-[1.2fr_1fr]">
            <div className="space-y-3 rounded-xl bg-panel-2 p-4 ring-1 ring-line">
              <div className="flex items-center gap-2">
                <PlatformBadge platform={r.source.platform} />
                <span className="font-semibold">{r.status.display_name || r.source.display_name || r.source.handle}</span>
              </div>
              {r.status.is_live ? (
                <div className="flex items-start gap-2 text-sm text-emerald-300"><CheckCircle2 size={16} className="mt-0.5 shrink-0" />
                  <div>{r.source.platform === "local" ? "Archivo válido" : "En directo ahora"}{r.status.title ? <> — <span className="text-ink">{r.status.title}</span></> : null}
                    {r.status.category && <div className="text-xs text-ink-2">Categoría de la plataforma: {r.status.category}</div>}
                  </div>
                </div>
              ) : r.status.error ? (
                <div className="flex items-start gap-2 text-sm text-amber-200"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{r.status.error}</div>
              ) : (
                <div className="flex items-start gap-2 text-sm text-ink-2"><Info size={16} className="mt-0.5 shrink-0" />
                  No está en directo. Se monitorizará y la captura empezará automáticamente cuando comience la transmisión.</div>
              )}
              <div className="text-[11px] text-ink-3">Estado consultado vía <b>{r.status.method}</b></div>
              <ul className="space-y-1 border-t border-line pt-3 text-xs text-ink-2">
                {r.capabilities.notes.map((n) => <li key={n} className="flex gap-1.5"><span className="text-ink-3">•</span>{n}</li>)}
              </ul>
            </div>
            <div className="space-y-3">
              <Field label="Modo">
                <div className="grid grid-cols-2 gap-2">
                  {(["review", "autopilot"] as const).map((m) => (
                    <button key={m} type="button" onClick={() => setCfg({ ...cfg, mode: m })}
                      className={`rounded-lg px-3 py-2 text-left text-xs ring-1 ${cfg.mode === m ? "bg-accent-soft ring-accent/60" : "bg-panel-2 ring-line"}`}>
                      <div className="font-semibold text-ink">{m === "review" ? "Review" : "Autopilot"}</div>
                      <div className="text-ink-3">{m === "review" ? "Cola de candidatos para aprobar" : "Genera clips automáticamente"}</div>
                    </button>
                  ))}
                </div>
              </Field>
              {cfg.mode === "autopilot" && (
                <Field label={`Puntuación mínima para generar: ${cfg.min_score}`}>
                  <input type="range" min={40} max={95} value={cfg.min_score} onChange={(e) => setCfg({ ...cfg, min_score: +e.target.value })} />
                </Field>
              )}
              <Field label={`Sensibilidad de detección: ${Math.round(cfg.sensitivity * 100)}%`} hint="Más sensibilidad = más candidatos (más revisión).">
                <input type="range" min={0} max={1} step={0.05} value={cfg.sensitivity} onChange={(e) => setCfg({ ...cfg, sensitivity: +e.target.value })} />
              </Field>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Buffer (s)"><input className="input" type="number" min={60} max={600} value={cfg.buffer_seconds} onChange={(e) => setCfg({ ...cfg, buffer_seconds: +e.target.value })} /></Field>
                <Field label="Antes (s)"><input className="input" type="number" min={5} max={120} value={cfg.pre_seconds} onChange={(e) => setCfg({ ...cfg, pre_seconds: +e.target.value })} /></Field>
                <Field label="Después (s)"><input className="input" type="number" min={3} max={60} value={cfg.post_seconds} onChange={(e) => setCfg({ ...cfg, post_seconds: +e.target.value })} /></Field>
              </div>
              <label className="flex items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={cfg.chat_enabled} onChange={(e) => setCfg({ ...cfg, chat_enabled: e.target.checked })} /> Analizar el chat cuando la plataforma lo permita</label>
              {r.source.platform === "local" && (
                <label className="flex items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={cfg.realtime} onChange={(e) => setCfg({ ...cfg, realtime: e.target.checked })} /> Procesar a velocidad de directo</label>
              )}
            </div>
          </div>
        )}

        {needsRights && (
          <label className="flex items-start gap-2.5 rounded-xl bg-amber-500/5 p-3 text-sm text-ink-2 ring-1 ring-amber-500/25">
            <input type="checkbox" className="mt-0.5" checked={rights} onChange={(e) => setRights(e.target.checked)} />
            <span>Confirmo que soy el creador o tengo su permiso para procesar esta transmisión y que respetaré los términos de la plataforma. El sistema solo guarda el buffer temporal y los fragmentos seleccionados.</span>
          </label>
        )}
        {add.error && <div className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-200 ring-1 ring-rose-500/30">{(add.error as Error).message}</div>}
        <div className="flex justify-end gap-2">
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={!r || (needsRights && !rights) || add.isPending} onClick={() => add.mutate()}>
            {add.isPending ? <Spinner /> : null} Añadir y monitorizar
          </button>
        </div>
      </div>
    </Modal>
  );
}
