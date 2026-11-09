import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Pause, Play, RefreshCw, Trash2, Radio } from "lucide-react";
import { channelState } from "./Dashboard";
import { Empty, Meter, PageHeader, PlatformBadge, Spinner, StatusPill, StepTracker, type Step } from "../components/ui";
import { api } from "../lib/api";
import { useEvents } from "../lib/events";
import { catLabel, compact, fmtUptime } from "../lib/format";
import type { Channel, SessionSnapshot } from "../lib/types";

export default function LiveStreams({ onAdd }: { onAdd: () => void }) {
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api.get<Channel[]>("/api/channels"), refetchInterval: 10000 });
  return (
    <div>
      <PageHeader title="Live Streams" subtitle="Monitoriza varios creadores a la vez. La captura empieza sola cuando el directo arranca."
        actions={<button className="btn btn-primary" onClick={onAdd}>Añadir transmisión</button>} />
      {channels.isLoading ? <Spinner /> : channels.data?.length ? (
        <div className="grid gap-4 2xl:grid-cols-2">{channels.data.map((c) => <StreamCard key={c.id} channel={c} />)}</div>
      ) : (
        <Empty icon={<Radio />} title="Ninguna transmisión monitorizada">Pega el enlace de un canal o directo de YouTube, Twitch o Kick.</Empty>
      )}
    </div>
  );
}

function StreamCard({ channel: c }: { channel: Channel }) {
  const qc = useQueryClient();
  const { sessions, transcripts, downloads } = useEvents();
  const ss: SessionSnapshot | null = sessions[c.id] ?? c.session;
  const dl = downloads[c.id];
  const refresh = () => qc.invalidateQueries({ queryKey: ["channels"] });
  const toggle = useMutation({ mutationFn: () => api.post(`/api/channels/${c.id}/${c.monitoring ? "stop" : "start"}`), onSettled: refresh });
  const check = useMutation({ mutationFn: () => api.post(`/api/channels/${c.id}/check`), onSettled: refresh });
  const del = useMutation({ mutationFn: () => api.del(`/api/channels/${c.id}`), onSettled: refresh });
  const patch = useMutation({ mutationFn: (config: Partial<Channel["config"]>) => api.patch(`/api/channels/${c.id}`, { config }), onSettled: refresh });
  const lines = transcripts[c.id] ?? [];
  const sig = ss?.signal ?? {};
  const ls = c.live_state ?? {};

  // Signature pipeline: Conexión/Descarga → Captura → Transcripción → Análisis → Candidatos → Clips
  const seg = ss?.segments ?? 0;
  const asrOn = !!ss?.asr.enabled;
  const asrLag = ss?.asr.lag_s ?? null;
  const combined = ss?.signal?.combined ?? 0;
  const cand = ss?.open_candidates ?? 0;
  const accepted = ss?.engine?.accepted ?? 0;
  const st = (done: boolean, active: boolean): Step["status"] => (done ? "done" : active ? "active" : "pending");
  const steps: Step[] = [
    { label: dl ? "Descarga" : "Conexión", status: ss ? "done" : dl ? "active" : c.monitoring ? "active" : "pending", detail: dl ? `${Math.round(dl.pct)}%` : ss ? "conectado" : undefined },
    { label: "Captura", status: st(seg > 2, !!ss && seg <= 2), detail: ss ? `${seg} seg.` : undefined },
    { label: "Transcripción", status: st(asrOn && (asrLag ?? 99) < 30, !!ss && asrOn), detail: asrOn && asrLag != null ? `−${asrLag.toFixed(0)}s` : undefined },
    { label: "Análisis", status: st(combined > 0.05, !!ss && seg > 2), detail: ss?.classification ? catLabel(ss.classification.primary) : undefined },
    { label: "Candidatos", status: st(cand > 0 || accepted > 0, !!ss && combined > 0.05), detail: ss ? String(cand) : undefined },
    { label: "Clips", status: st(accepted > 0, !!ss && cand > 0), detail: accepted > 0 ? String(accepted) : undefined },
  ];
  const showPipeline = c.monitoring || !!ss || !!dl;

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        {c.avatar_url ? <img src={c.avatar_url} className="h-10 w-10 rounded-full object-cover ring-1 ring-line" alt="" /> :
          <div className="grid h-10 w-10 place-items-center rounded-full bg-panel-3 text-sm font-bold text-ink-2">{c.display_name.slice(0, 2).toUpperCase()}</div>}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="truncate font-semibold">{c.display_name}</span><PlatformBadge platform={c.platform} /><StatusPill state={channelState(c, ss ?? undefined)} /></div>
          <div className="mt-0.5 truncate text-xs text-ink-3">{ls.title ?? c.url}</div>
        </div>
        <div className="flex gap-1.5">
          <button className="btn btn-ghost p-2" title="Comprobar estado" onClick={() => check.mutate()}>{check.isPending ? <Spinner /> : <RefreshCw size={16} />}</button>
          <button className={`btn ${c.monitoring ? "" : "btn-primary"}`} onClick={() => toggle.mutate()} disabled={toggle.isPending}>
            {c.monitoring ? <><Pause size={15} /> Detener</> : <><Play size={15} /> Monitorizar</>}
          </button>
          <button className="btn btn-ghost btn-danger p-2" title="Eliminar" onClick={() => confirm(`¿Eliminar ${c.display_name}?`) && del.mutate()}><Trash2 size={16} /></button>
        </div>
      </div>

      {showPipeline && (
        <div className="border-b border-line bg-panel-2/50 px-5 py-4">
          <StepTracker steps={steps} />
          {dl && !ss && (
            <div className="mt-3 flex items-center gap-3 text-xs">
              <span className="whitespace-nowrap text-ink-2">{dl.phase === "merging" ? "Uniendo vídeo…" : "Descargando vídeo…"}</span>
              <div className="flex-1"><Meter value={dl.pct} /></div>
              <span className="tabular w-10 text-right text-ink-3">{Math.round(dl.pct)}%</span>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-5 p-5 md:grid-cols-2">
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <Info k="Categoría detectada" v={ss?.classification ? catLabel(ss.classification.primary) : "—"} />
            <Info k="Secundarias" v={ss?.classification?.secondary.map(catLabel).join(", ") || "—"} />
            <Info k="Categoría plataforma" v={ls.category ?? "—"} />
            <Info k="Espectadores" v={ls.viewer_count != null ? compact(ls.viewer_count) : "—"} />
            <Info k="Tiempo activo" v={ss ? fmtUptime(ss.uptime_s) : "—"} />
            <Info k="Capturado" v={ss ? `${Math.round(ss.seconds_captured)} s · ${ss.segments} seg.` : "—"} />
            <Info k="Buffer" v={ss ? `${Math.round(ss.buffer.seconds)} s (${ss.buffer.pins} reservas)` : "—"} />
            <Info k="ASR" v={ss?.asr.enabled ? `retraso ${ss.asr.lag_s?.toFixed(1) ?? "—"} s · ${ss.asr.language ?? "…"}` : "—"} />
            <Info k="Duplicados/anuncios" v={ss ? `${ss.capture.duplicates_skipped ?? 0} / ${ss.capture.ads_skipped ?? 0}` : "—"} />
            <Info k="Reconexiones" v={ss ? String(ss.capture.reconnects ?? 0) : "—"} />
            <Info k="Chat" v={ss ? (ss.chat.connected ? `${ss.chat.messages} msgs${ss.chat.official === false ? " (no oficial)" : ""}` : ss.chat.error ?? "no disponible") : "—"} />
            <Info k="Momentos" v={ss ? `${ss.engine.accepted ?? 0} aceptados · ${ss.open_candidates} abiertos` : "—"} />
          </div>
          {ls.error && <div className="rounded-lg bg-amber-500/10 p-2.5 text-xs text-amber-200 ring-1 ring-amber-500/25">{ls.error}</div>}
          {ss?.error && <div className="rounded-lg bg-rose-500/10 p-2.5 text-xs text-rose-200 ring-1 ring-rose-500/25">{ss.error}</div>}
          <div className="text-[11px] text-ink-3">Estado vía {ls.method ?? "—"}{ls.checked_at ? ` · ${new Date(ls.checked_at * 1000).toLocaleTimeString()}` : ""}</div>
        </div>

        <div className="space-y-3">
          <div className="label">Señales en vivo</div>
          {(["emotion", "chat", "visual", "lexical", "combined"] as const).map((k) => (
            <div key={k} className="flex items-center gap-3 text-xs">
              <span className="w-20 text-ink-2">{{ emotion: "Voz/emoción", chat: "Chat", visual: "Visual", lexical: "Léxico", combined: "Combinada" }[k]}</span>
              <Meter value={(sig[k] ?? 0) * 100} />
              <span className="tabular w-8 text-right text-ink-3">{Math.round((sig[k] ?? 0) * 100)}</span>
            </div>
          ))}
          <div className="grid grid-cols-2 gap-3 border-t border-line pt-3">
            <div>
              <div className="label mb-1.5">Modo</div>
              <div className="flex gap-1 rounded-lg bg-panel-2 p-1 ring-1 ring-line">
                {(["review", "autopilot"] as const).map((m) => (
                  <button key={m} onClick={() => patch.mutate({ mode: m })}
                    className={`flex-1 rounded-md px-2 py-1 text-xs font-semibold ${c.config.mode === m ? "bg-accent text-white" : "text-ink-2"}`}>
                    {m === "review" ? "Review" : "Autopilot"}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div className="label mb-1.5">Sensibilidad {Math.round(c.config.sensitivity * 100)}%</div>
              <input type="range" min={0} max={1} step={0.05} defaultValue={c.config.sensitivity} onMouseUp={(e) => patch.mutate({ sensitivity: +(e.target as HTMLInputElement).value })} />
            </div>
            {c.config.mode === "autopilot" && (
              <div className="col-span-2">
                <div className="label mb-1.5">Puntuación mínima autopilot: {c.config.min_score}</div>
                <input type="range" min={40} max={95} defaultValue={c.config.min_score} onMouseUp={(e) => patch.mutate({ min_score: +(e.target as HTMLInputElement).value })} />
              </div>
            )}
          </div>
        </div>
      </div>

      {(lines.length > 0 || ss?.transcript_tail) && (
        <div className="border-t border-line bg-panel-2/60 px-5 py-3">
          <div className="label mb-1.5 flex items-center gap-1.5"><MessageSquare size={12} /> Transcripción en vivo</div>
          <div className="max-h-28 overflow-y-auto text-sm leading-relaxed text-ink-2">
            {lines.length ? lines.slice(-6).map((l, i) => (
              <p key={i}><span className="tabular mr-2 text-[11px] text-ink-3">{l.speaker ?? ""} {Math.floor(l.start)}s</span>{l.text}</p>
            )) : <p>{ss?.transcript_tail}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function Info({ k, v }: { k: string; v: string }) {
  return <div className="min-w-0"><div className="text-ink-3">{k}</div><div className="truncate text-ink">{v}</div></div>;
}
