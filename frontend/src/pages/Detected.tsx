import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, Sparkles, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { FeedbackBar } from "../components/FeedbackBar";
import { Chip, Empty, PageHeader, PlatformBadge, PriorityBadge, ScoreRing, Spinner, SubscoreBars, Tabs } from "../components/ui";
import { api, qs, withToken } from "../lib/api";
import { catLabel, fmtDuration, timeAgo, TYPE_LABELS } from "../lib/format";
import type { Channel, MomentSummary, Paged } from "../lib/types";

type StatusTab = "pending" | "approved,rendered" | "rejected" | "";

export default function Detected() {
  const [status, setStatus] = useState<StatusTab>("pending");
  const [priority, setPriority] = useState<string>("");
  const [channel, setChannel] = useState<string>("");
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api.get<Channel[]>("/api/channels") });
  const moments = useQuery({
    queryKey: ["moments", status, priority, channel],
    queryFn: () => api.get<Paged<MomentSummary>>(`/api/moments${qs({ status: status || undefined, priority: priority || undefined, channel_id: channel || undefined, limit: 100 })}`),
    refetchInterval: 10000,
  });
  const items = [...(moments.data?.items ?? [])].sort((a, b) => (status === "pending" ? b.score - a.score : b.created_at - a.created_at));

  return (
    <div>
      <PageHeader title="Clips detectados" subtitle="Momentos candidatos encontrados en vivo. Aprueba, rechaza o edita: cada decisión entrena el sistema." />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Tabs<StatusTab> value={status} onChange={setStatus} tabs={[
          { id: "pending", label: "Pendientes" }, { id: "approved,rendered", label: "Aprobados" }, { id: "rejected", label: "Rechazados" }, { id: "", label: "Todos" }]} />
        <div className="flex flex-wrap gap-1.5">
          {[["", "Cualquier prioridad"], ["excellent", "90-100 Excelente"], ["high", "75-89 Alta"], ["review", "60-74 Revisar"], ["low", "<60 Baja"]].map(([v, l]) => (
            <Chip key={v} active={priority === v} onClick={() => setPriority(v)}>{l}</Chip>
          ))}
        </div>
        <select className="input w-auto" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">Todos los creadores</option>
          {channels.data?.map((c) => <option key={c.id} value={c.id}>{c.display_name}</option>)}
        </select>
      </div>
      {moments.isLoading ? <Spinner /> : items.length ? (
        <div className="space-y-4">{items.map((m) => <MomentCard key={m.id} m={m} />)}</div>
      ) : (
        <Empty icon={<Sparkles />} title="No hay momentos en esta vista">Los candidatos aparecen aquí segundos después de ocurrir en el directo.</Empty>
      )}
    </div>
  );
}

function MomentCard({ m }: { m: MomentSummary }) {
  const ref = useRef<HTMLVideoElement>(null);
  const a = m.start - m.source_start;
  const b = m.end - m.source_start;
  const onTime = () => {
    const v = ref.current;
    if (v && v.currentTime >= b) v.pause();
  };
  const ctx = m.context ?? {};
  const hookIdx = m.hook ? m.transcript.indexOf(m.hook) : -1;

  return (
    <div className="card grid gap-5 p-4 md:grid-cols-[200px_1fr] xl:grid-cols-[200px_1fr_300px]">
      <div className="relative mx-auto aspect-[9/16] w-full max-w-[200px] overflow-hidden rounded-xl bg-black ring-1 ring-line">
        {m.source_url ? (
          <video ref={ref} src={`${withToken(m.source_url)}#t=${a.toFixed(2)}`} poster={withToken(m.thumbnail_url)} controls preload="none"
            className="h-full w-full object-cover" onTimeUpdate={onTime} onPlay={(e) => { if (e.currentTarget.currentTime < a - 0.5 || e.currentTarget.currentTime >= b) e.currentTarget.currentTime = a; }} />
        ) : <div className="grid h-full place-items-center text-xs text-ink-3">Medio no disponible</div>}
      </div>

      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-3">
          <ScoreRing score={m.score} />
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-semibold leading-snug">{m.title}</h3>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
              <PriorityBadge priority={m.priority} />
              {m.channel && <PlatformBadge platform={m.channel.platform} />}
              <span>{m.channel?.display_name}</span>·<span>{catLabel(m.category)}</span>·<span className="tabular">{fmtDuration(m.duration)}</span>·
              <span>confianza {Math.round(m.confidence * 100)}%</span>·<span>{timeAgo(m.created_at)}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {m.moment_types.map((t) => <span key={t} className="rounded-md bg-panel-3 px-2 py-0.5 text-[11px] text-ink-2 ring-1 ring-line">{TYPE_LABELS[t] ?? t}</span>)}
        </div>
        <p className="line-clamp-5 text-sm leading-relaxed text-ink-2">
          {hookIdx >= 0 ? (<>{m.transcript.slice(0, hookIdx)}<mark className="rounded bg-accent-soft px-0.5 text-ink">{m.hook}</mark>{m.transcript.slice(hookIdx + (m.hook?.length ?? 0))}</>) : m.transcript || <i>Sin voz (momento visual/sonoro)</i>}
        </p>
        <div className="grid gap-2 rounded-xl bg-panel-2 p-3 text-xs ring-1 ring-line sm:grid-cols-2">
          <Ctx k="Qué sucede" v={ctx.what} />
          <Ctx k="Quién habla" v={ctx.who} />
          <Ctx k="Por qué interesa" v={ctx.why || m.reasons.join(" · ")} />
          <div className="flex flex-wrap items-center gap-3 text-ink-2">
            <Flag ok={ctx.has_beginning} label="Inicio claro" /><Flag ok={ctx.has_ending} label="Desenlace" /><Flag ok={ctx.standalone} label="Se entiende solo" />
            <span className="text-ink-3">análisis: {ctx.source === "llm" ? "LLM" : "multiseñal"}</span>
          </div>
        </div>
        {m.status === "pending" ? <FeedbackBar momentId={m.id} /> : (
          <div className="text-xs text-ink-3">Estado: <b className="text-ink-2">{m.status}</b></div>
        )}
      </div>

      <div className="md:col-span-2 xl:col-span-1">
        <div className="label mb-2">Puntuación por criterio</div>
        <SubscoreBars subscores={m.subscores} points={m.points} weights={m.weights} />
        {m.model_version && <div className="mt-3 text-[11px] text-ink-3">Ajustado por modelo {m.model_version}</div>}
      </div>
    </div>
  );
}

function Ctx({ k, v }: { k: string; v?: string }) {
  return <div><div className="text-ink-3">{k}</div><div className="text-ink-2">{v || "—"}</div></div>;
}

function Flag({ ok, label }: { ok?: boolean; label: string }) {
  if (ok === undefined) return <span className="flex items-center gap-1 text-ink-3"><CircleHelp size={13} />{label}</span>;
  return ok ? <span className="flex items-center gap-1 text-emerald-300"><CheckCircle2 size={13} />{label}</span>
    : <span className="flex items-center gap-1 text-amber-300"><XCircle size={13} />{label}</span>;
}
