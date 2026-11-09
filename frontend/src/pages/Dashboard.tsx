import { useQuery } from "@tanstack/react-query";
import { Activity, Clapperboard, Cpu, Film, HardDrive, Layers, Radio, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { Empty, Meter, PageHeader, PlatformBadge, PriorityBadge, ScoreRing, StatTile, StatusPill } from "../components/ui";
import { api, withToken } from "../lib/api";
import { useEvents } from "../lib/events";
import { catLabel, fmtDuration, fmtUptime, timeAgo } from "../lib/format";
import type { Channel, MomentSummary, Paged, Platform, SystemStatus } from "../lib/types";

export function channelState(c: Channel, live?: { state: string }): string {
  if (live) return live.state;
  if (!c.monitoring) return "offline";
  if (c.live_state?.error) return "error";
  return "waiting";
}

export default function Dashboard({ onAdd }: { onAdd: () => void }) {
  const { sessions } = useEvents();
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/system/status"), refetchInterval: 3000 });
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api.get<Channel[]>("/api/channels"), refetchInterval: 15000 });
  const best = useQuery({ queryKey: ["moments", "best"], queryFn: () => api.get<Paged<MomentSummary>>("/api/moments?limit=60") });
  const s = status.data;
  const counts = s?.counts ?? {};
  const live = { ...(s?.sessions ?? {}), ...Object.fromEntries(Object.entries(sessions).map(([k, v]) => [String(k), v])) };
  const platforms = new Map<Platform, number>();
  (channels.data ?? []).forEach((c) => platforms.set(c.platform, (platforms.get(c.platform) ?? 0) + 1));
  const top = [...(best.data?.items ?? [])].filter((m) => m.status !== "discarded").sort((a, b) => b.score - a.score).slice(0, 6);
  const r = s?.resources;

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" subtitle="Estado de la captura, del análisis y de la generación de clips en tiempo real." />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Plataformas conectadas" value={platforms.size} icon={<Layers size={15} />}
          hint={<div className="mt-1 flex flex-wrap gap-1">{[...platforms.keys()].map((p) => <PlatformBadge key={p} platform={p} />)}</div>} />
        <StatTile label="Canales monitorizados" value={counts.monitoring ?? 0} hint={`${counts.channels ?? 0} canales en total`} icon={<Radio size={15} />} />
        <StatTile label="Procesando en vivo" value={Object.keys(live).length} hint="con conexión real activa" icon={<Activity size={15} />} />
        <StatTile label="Clips generados" value={counts.clips_ready ?? 0} hint={`${counts.clips ?? 0} creados`} icon={<Film size={15} />} />
        <StatTile label="Pendientes de revisión" value={counts.pending_review ?? 0} hint={<Link to="/detected" className="text-accent-2 hover:underline">Abrir cola</Link>} icon={<Sparkles size={15} />} />
        <StatTile label="Renders en curso" value={counts.clips_rendering ?? 0} hint={`cola: ${Object.values(s?.queue ?? {}).reduce((a, b) => a + b, 0)} · ${s?.queue_backend ?? "local"}`} icon={<Clapperboard size={15} />} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
            <h2 className="text-sm font-semibold">Transmisiones</h2>
            <Link to="/live" className="text-xs text-accent-2 hover:underline">Ver todas</Link>
          </div>
          {channels.data?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase tracking-wider text-ink-3">
                  <tr><th className="px-5 py-2.5 font-medium">Canal</th><th className="px-3 font-medium">Estado</th><th className="px-3 font-medium">Categoría</th>
                    <th className="px-3 font-medium">Activo</th><th className="px-3 font-medium">Retraso ASR</th><th className="px-3 font-medium">Momentos</th></tr>
                </thead>
                <tbody>
                  {channels.data.map((c) => {
                    const ss = live[String(c.id)];
                    return (
                      <tr key={c.id} className="border-t border-line/70">
                        <td className="px-5 py-3"><div className="flex items-center gap-2.5">
                          <PlatformBadge platform={c.platform} /><span className="max-w-[180px] truncate font-medium">{c.display_name}</span></div></td>
                        <td className="px-3"><StatusPill state={channelState(c, ss)} /></td>
                        <td className="px-3 text-ink-2">{ss?.classification ? catLabel(ss.classification.primary) : c.live_state?.category ?? "—"}</td>
                        <td className="tabular px-3 text-ink-2">{ss ? fmtUptime(ss.uptime_s) : "—"}</td>
                        <td className="tabular px-3 text-ink-2">{ss?.asr.lag_s != null ? `${ss.asr.lag_s.toFixed(1)} s` : "—"}</td>
                        <td className="tabular px-3 text-ink-2">{ss ? `${ss.engine.accepted ?? 0} / ${ss.engine.candidates ?? 0}` : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-5"><Empty icon={<Radio />} title="Sin transmisiones">
              Añade un canal de YouTube, Twitch o Kick, un archivo local o una ingesta RTMP.
              <div className="mt-3"><button className="btn btn-primary" onClick={onAdd}>Añadir transmisión</button></div>
            </Empty></div>
          )}
        </section>

        <section className="card p-5">
          <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold"><Cpu size={15} className="text-ink-3" /> Recursos</h2>
          {r ? (
            <div className="space-y-4 text-sm">
              <Res label="CPU" value={r.cpu_percent} detail={`${r.cpu_percent.toFixed(0)}% · ${r.cpu_count} núcleos`} />
              <Res label="RAM" value={r.ram_percent} detail={`${(r.ram_used_mb / 1024).toFixed(1)} / ${(r.ram_total_mb / 1024).toFixed(1)} GB · proceso ${r.process_rss_mb} MB`} />
              {r.gpus.length ? r.gpus.map((g) => (
                <Res key={g.index} label={`GPU ${g.index}`} value={g.util_percent} detail={`${g.name} · ${g.util_percent}% · ${(g.mem_used_mb / 1024).toFixed(1)}/${(g.mem_total_mb / 1024).toFixed(1)} GB`} />
              )) : <div className="text-xs text-ink-3">GPU: no detectada (CUDA {r.cuda ? "sí" : "no"}) — ASR y render en CPU.</div>}
              <Res label="Disco" value={100 - (r.disk_free_gb / r.disk_total_gb) * 100} detail={`${r.disk_free_gb} GB libres`} icon={<HardDrive size={12} />} />
              <div className="border-t border-line pt-3 text-xs text-ink-2">
                <div>ASR <b className="text-ink">{s?.asr.model}</b> {s?.asr.loaded ? `· ${s.asr.device}/${s.asr.compute_type}` : "· se carga al primer directo"}
                  {s?.asr.rtf ? ` · RTF ${s.asr.rtf}` : ""}</div>
                {s?.asr.error && <div className="text-rose-300">ASR: {s.asr.error}</div>}
                <div>Comprensión contextual: <b className="text-ink">{s?.llm.provider === "none" ? "heurística multiseñal" : `${s?.llm.provider} · ${s?.llm.model}`}</b></div>
              </div>
            </div>
          ) : <div className="text-sm text-ink-3">Cargando…</div>}
        </section>
      </div>

      <section className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Mejores momentos detectados</h2>
          <Link to="/detected" className="text-xs text-accent-2 hover:underline">Revisar</Link>
        </div>
        {top.length ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {top.map((m) => (
              <Link key={m.id} to="/detected" className="flex gap-3 rounded-xl bg-panel-2 p-3 ring-1 ring-line transition hover:ring-line-2">
                <div className="relative h-24 w-16 shrink-0 overflow-hidden rounded-lg bg-panel-3">
                  {m.thumbnail_url && <img src={withToken(m.thumbnail_url)} className="h-full w-full object-cover" alt="" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="line-clamp-2 text-sm font-medium">{m.title}</div>
                    <ScoreRing score={m.score} size={40} />
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-3">
                    <PriorityBadge priority={m.priority} /> {catLabel(m.category)} · {fmtDuration(m.duration)} · {timeAgo(m.created_at)}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : <div className="text-sm text-ink-3">Aún no hay momentos. Aparecerán aquí mientras se analizan las transmisiones activas.</div>}
      </section>
    </div>
  );
}

function Res({ label, value, detail, icon }: { label: string; value: number; detail: string; icon?: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs"><span className="flex items-center gap-1 font-medium text-ink-2">{icon}{label}</span><span className="tabular text-ink-3">{detail}</span></div>
      <Meter value={value} invert />
    </div>
  );
}
