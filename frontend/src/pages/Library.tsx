import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Film, Pencil, Play, RefreshCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { FeedbackBar } from "../components/FeedbackBar";
import { Empty, Modal, PageHeader, PlatformBadge, ScoreRing, Spinner, StatusPill } from "../components/ui";
import { api, qs, withToken } from "../lib/api";
import { useEvents } from "../lib/events";
import { CATEGORY_LABELS, catLabel, fmtDate, fmtDuration } from "../lib/format";
import type { Channel, ClipSummary, Paged } from "../lib/types";

const DAY = 86400;

export default function LibraryPage() {
  const [f, setF] = useState({ channel_id: "", platform: "", category: "", status: "", review: "", min_score: "", min_duration: "", max_duration: "", since: "", q: "" });
  const [playing, setPlaying] = useState<ClipSummary | null>(null);
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api.get<Channel[]>("/api/channels") });
  const clips = useQuery({
    queryKey: ["clips", f],
    queryFn: () => api.get<Paged<ClipSummary>>(`/api/clips${qs({ ...f, since: f.since ? Date.now() / 1000 - +f.since * DAY : "", limit: 120 })}`),
    refetchInterval: 15000,
  });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  return (
    <div>
      <PageHeader title="Biblioteca" subtitle={`${clips.data?.total ?? 0} clips verticales 1080×1920 · H.264 / AAC`} />
      <div className="card mb-5 grid gap-2 p-3 sm:grid-cols-2 lg:grid-cols-5 2xl:grid-cols-10">
        <input className="input lg:col-span-2" placeholder="Buscar por título" value={f.q} onChange={set("q")} />
        <select className="input" value={f.channel_id} onChange={set("channel_id")}><option value="">Streamer</option>{channels.data?.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}</select>
        <select className="input" value={f.platform} onChange={set("platform")}><option value="">Plataforma</option>{["youtube", "twitch", "kick", "local", "vod", "ingest"].map((p) => <option key={p}>{p}</option>)}</select>
        <select className="input" value={f.category} onChange={set("category")}><option value="">Categoría</option>{Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select className="input" value={f.since} onChange={set("since")}><option value="">Cualquier fecha</option><option value="1">Últimas 24 h</option><option value="7">7 días</option><option value="30">30 días</option></select>
        <select className="input" value={f.min_score} onChange={set("min_score")}><option value="">Puntuación</option><option value="90">≥ 90</option><option value="75">≥ 75</option><option value="60">≥ 60</option></select>
        <select className="input" value={`${f.min_duration}-${f.max_duration}`} onChange={(e) => { const [a, b] = e.target.value.split("-"); setF({ ...f, min_duration: a, max_duration: b }); }}>
          <option value="-">Duración</option><option value="0-30">≤ 30 s</option><option value="30-60">30–60 s</option><option value="60-">≥ 60 s</option></select>
        <select className="input" value={f.status} onChange={set("status")}><option value="">Estado</option><option value="ready">Listo</option><option value="queued,rendering">Procesando</option><option value="failed">Falló</option><option value="draft">Borrador</option></select>
        <select className="input" value={f.review} onChange={set("review")}><option value="">Aprobación</option><option value="approved">Aprobados</option><option value="rejected">Rechazados</option><option value="unreviewed">Sin revisar</option></select>
      </div>
      {clips.isLoading ? <Spinner /> : clips.data?.items.length ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
          {clips.data.items.map((c) => <ClipCard key={c.id} c={c} onPlay={() => setPlaying(c)} />)}
        </div>
      ) : <Empty icon={<Film />} title="La biblioteca está vacía">Aprueba momentos o activa Autopilot para generar clips.</Empty>}
      {playing && (
        <Modal open onClose={() => setPlaying(null)} title={playing.title}>
          <div className="grid gap-4 sm:grid-cols-[260px_1fr]">
            <video src={withToken(playing.video_url)} controls autoPlay className="aspect-[9/16] w-full rounded-xl bg-black" />
            <div className="space-y-3">
              <div className="text-sm text-ink-2">{playing.channel?.display_name} · {catLabel(playing.category)} · {fmtDuration(playing.duration)}</div>
              <FeedbackBar clipId={playing.id} onDone={() => setPlaying(null)} />
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function ClipCard({ c, onPlay }: { c: ClipSummary; onPlay: () => void }) {
  const qc = useQueryClient();
  const { clipProgress } = useEvents();
  const [hover, setHover] = useState(false);
  const del = useMutation({ mutationFn: () => api.del(`/api/clips/${c.id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["clips"] }) });
  const rerender = useMutation({ mutationFn: () => api.post(`/api/clips/${c.id}/render`), onSuccess: () => qc.invalidateQueries({ queryKey: ["clips"] }) });
  const progress = clipProgress[c.id] ?? c.progress;

  return (
    <div className="group card overflow-hidden">
      <div className="relative aspect-[9/16] bg-black" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        {hover && c.video_url ? <video src={withToken(c.video_url)} autoPlay muted loop className="h-full w-full object-cover" /> :
          c.thumbnail_url ? <img src={withToken(c.thumbnail_url)} className="h-full w-full object-cover" alt="" /> : null}
        <div className="absolute left-2 top-2 rounded-full bg-black/65 backdrop-blur">{c.score != null && <ScoreRing score={c.score} size={38} />}</div>
        <div className="absolute right-2 top-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-semibold tabular">{fmtDuration(c.duration)}</div>
        {c.status !== "ready" && (
          <div className="absolute inset-x-2 bottom-2 rounded-lg bg-black/75 p-2 backdrop-blur">
            <StatusPill state={c.status} />
            {c.status === "rendering" && <div className="mt-1.5 h-1 overflow-hidden rounded bg-white/10"><div className="h-full bg-accent" style={{ width: `${progress * 100}%` }} /></div>}
            {c.error && <div className="mt-1 line-clamp-2 text-[10px] text-rose-300">{c.error}</div>}
          </div>
        )}
        {c.status === "ready" && (
          <button onClick={onPlay} className="absolute inset-0 grid place-items-center opacity-0 transition group-hover:opacity-100" aria-label="Reproducir">
            <span className="grid h-12 w-12 place-items-center rounded-full bg-black/60 backdrop-blur"><Play size={22} className="fill-white" /></span>
          </button>
        )}
      </div>
      <div className="space-y-1.5 p-3">
        <div className="line-clamp-2 text-sm font-medium leading-snug">{c.title}</div>
        <div className="flex flex-wrap items-center gap-1 text-[11px] text-ink-3">
          {c.channel && <PlatformBadge platform={c.channel.platform} />}<span className="truncate">{c.channel?.display_name}</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-ink-3">
          <span>{catLabel(c.category)}</span><span>{fmtDate(c.created_at)}</span>
        </div>
        {c.review !== "unreviewed" && <div className={`text-[11px] font-semibold ${c.review === "approved" ? "text-emerald-300" : "text-rose-300"}`}>{c.review === "approved" ? "Aprobado" : "Rechazado"}</div>}
        <div className="flex gap-1 pt-1">
          <Link to={`/editor/${c.id}`} className="btn btn-ghost flex-1 justify-center px-2 py-1.5" title="Editar"><Pencil size={14} /></Link>
          {c.video_url && <a href={withToken(`/api/clips/${c.id}/download`)} className="btn btn-ghost flex-1 justify-center px-2 py-1.5" title="Descargar / exportar"><Download size={14} /></a>}
          {(c.status === "failed" || c.status === "draft") && <button className="btn btn-ghost flex-1 justify-center px-2 py-1.5" title="Renderizar" onClick={() => rerender.mutate()}><RefreshCcw size={14} /></button>}
          <button className="btn btn-ghost btn-danger flex-1 justify-center px-2 py-1.5" title="Eliminar" onClick={() => confirm("¿Eliminar este clip?") && del.mutate()}><Trash2 size={14} /></button>
        </div>
      </div>
    </div>
  );
}
