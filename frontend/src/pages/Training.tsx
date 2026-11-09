import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, BrainCircuit, CheckCircle2, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { Chip, Empty, Field, PageHeader, Spinner, StatusPill } from "../components/ui";
import { api, withToken } from "../lib/api";
import { catLabel, fmtDate, fmtDuration, TYPE_LABELS } from "../lib/format";
import type { ModelVersion, TrainingExample } from "../lib/types";

const LABELS: Record<string, string> = { excellent: "Excelente", good: "Bueno", normal: "Normal", bad: "Malo", unpublishable: "No publicable" };
const TAGS: Record<string, string> = {
  gracioso: "Gracioso", buen_gancho: "Buen gancho", historia_interesante: "Historia interesante", reaccion: "Reacción", polemica: "Polémica",
  revelacion: "Revelación", buen_contexto: "Buen contexto", aburrido: "Aburrido", mal_cortado: "Mal cortado", demasiado_largo: "Demasiado largo", falta_contexto: "Falta contexto",
};

export default function Training() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["training"], queryFn: () => api.get<{ items: TrainingExample[] }>("/api/training"), refetchInterval: 8000 });
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ label: "good", tags: [] as string[], title: "", creator: "", platform: "" });
  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("label", form.label);
      fd.append("tags", form.tags.join(","));
      fd.append("title", form.title);
      fd.append("creator", form.creator);
      fd.append("platform", form.platform);
      return api.form<TrainingExample>("/api/training", fd);
    },
    onSuccess: () => { setFile(null); setForm({ ...form, title: "", tags: [] }); qc.invalidateQueries({ queryKey: ["training"] }); },
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Training Library" subtitle="Ejemplos de referencia propios, positivos y negativos. Se analizan con el mismo pipeline que los directos y alimentan el modelo de selección." />
      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <div className="space-y-6">
          <section className="card space-y-4 p-5">
            <h2 className="flex items-center gap-2 text-sm font-semibold"><Upload size={15} /> Importar clip</h2>
            <label className="grid cursor-pointer place-items-center rounded-xl border border-dashed border-line-2 bg-panel-2 px-4 py-6 text-center text-sm text-ink-2 hover:border-accent">
              <input type="file" accept="video/*" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              {file ? <span className="text-ink">{file.name}</span> : <>Selecciona un vídeo (mp4, mov, webm)<span className="text-xs text-ink-3">Usa solo clips propios o con permiso</span></>}
            </label>
            <Field label="Calificación"><div className="flex flex-wrap gap-1.5">{Object.entries(LABELS).map(([k, v]) => <Chip key={k} active={form.label === k} onClick={() => setForm({ ...form, label: k })}>{v}</Chip>)}</div></Field>
            <Field label="Etiquetas"><div className="flex flex-wrap gap-1.5">{Object.entries(TAGS).map(([k, v]) => (
              <Chip key={k} active={form.tags.includes(k)} onClick={() => setForm({ ...form, tags: form.tags.includes(k) ? form.tags.filter((x) => x !== k) : [...form.tags, k] })}>{v}</Chip>
            ))}</div></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Título"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
              <Field label="Creador"><input className="input" value={form.creator} onChange={(e) => setForm({ ...form, creator: e.target.value })} /></Field>
            </div>
            <button className="btn btn-primary w-full justify-center" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? <Spinner /> : <Upload size={15} />} Importar y analizar</button>
            {upload.error && <div className="text-xs text-rose-300">{(upload.error as Error).message}</div>}
          </section>
          <ModelPanel />
        </div>
        <section className="space-y-3">
          {list.isLoading ? <Spinner /> : list.data?.items.length ? list.data.items.map((e) => <ExampleRow key={e.id} e={e} />) : (
            <Empty icon={<BookOpenCheck />} title="Sin ejemplos">Importa clips que consideres excelentes o malos para enseñar al sistema tu criterio editorial.</Empty>
          )}
        </section>
      </div>
    </div>
  );
}

function ExampleRow({ e }: { e: TrainingExample }) {
  const qc = useQueryClient();
  const patch = useMutation({ mutationFn: (b: Partial<TrainingExample>) => api.patch(`/api/training/${e.id}`, b), onSuccess: () => qc.invalidateQueries({ queryKey: ["training"] }) });
  const del = useMutation({ mutationFn: () => api.del(`/api/training/${e.id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["training"] }) });
  const a = e.analysis ?? {};
  return (
    <div className="card grid gap-4 p-4 md:grid-cols-[110px_1fr]">
      <video src={withToken(e.video_url)} className="aspect-[9/16] w-full rounded-lg bg-black object-cover" controls preload="none" />
      <div className="min-w-0 space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{e.title}</span>
          {e.status !== "ready" && <StatusPill state={e.status === "failed" ? "failed" : e.status === "pending" ? "queued" : "rendering"} />}
          <span className="text-xs text-ink-3">{e.creator ?? "—"} · {fmtDuration(e.duration)} · {fmtDate(e.created_at)}</span>
          <button className="btn btn-ghost btn-danger ml-auto p-1.5" onClick={() => confirm("¿Eliminar ejemplo?") && del.mutate()}><Trash2 size={14} /></button>
        </div>
        <div className="flex flex-wrap gap-1.5">{Object.entries(LABELS).map(([k, v]) => <Chip key={k} active={e.label === k} onClick={() => patch.mutate({ label: k })}>{v}</Chip>)}</div>
        <div className="flex flex-wrap gap-1">{e.tags.map((t) => <span key={t} className="rounded bg-panel-3 px-1.5 py-0.5 text-[11px] text-ink-2">{TAGS[t] ?? t}</span>)}</div>
        {e.status === "ready" && (
          <div className="grid gap-2 rounded-xl bg-panel-2 p-3 text-xs text-ink-2 ring-1 ring-line sm:grid-cols-3">
            <div><div className="text-ink-3">Tipo de contenido</div>{catLabel(a.category)} · {(a.moment_types ?? []).map((t: string) => TYPE_LABELS[t] ?? t).join(", ")}</div>
            <div><div className="text-ink-3">Gancho ({Math.round((a.hook?.score ?? 0) * 100)}%)</div><span className="line-clamp-2">{a.hook?.text ?? "—"}</span></div>
            <div><div className="text-ink-3">Narrativa</div>{Math.round((a.narrative?.score ?? 0) * 100)}% · {a.narrative?.speakers ?? 1} voces</div>
            <div><div className="text-ink-3">Ritmo</div>{a.pacing?.words_per_second ?? "—"} palabras/s · silencio {Math.round((a.pacing?.silence_ratio ?? 0) * 100)}%</div>
            <div><div className="text-ink-3">Emociones</div>{(a.emotions?.events ?? []).slice(0, 5).join(", ") || "—"}</div>
            <div><div className="text-ink-3">Edición</div>{a.editing?.vertical ? "Vertical" : "Horizontal"} · {a.editing?.scene_cuts_per_min} cortes/min · captions {Math.round((a.editing?.burned_captions ?? 0) * 100)}%</div>
          </div>
        )}
        {e.error && <div className="text-xs text-rose-300">{e.error}</div>}
      </div>
    </div>
  );
}

function ModelPanel() {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.get<{ items: ModelVersion[]; production: string | null; blend_alpha: number }>("/api/learning/models") });
  const train = useMutation({ mutationFn: () => api.post<Record<string, any>>("/api/learning/train", { kind: "editorial" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }) });
  const promote = useMutation({ mutationFn: (id: number) => api.post(`/api/learning/models/${id}/promote`), onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }) });
  const r = train.data;
  return (
    <section className="card space-y-3 p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><BrainCircuit size={15} /> Modelo de selección</h2>
      <p className="text-xs text-ink-3">Regresión logística ligera con preferencias globales, por creador y por categoría. Un modelo nuevo solo pasa a producción si supera al actual en validación cruzada.</p>
      <div className="text-xs text-ink-2">En producción: <b className="text-ink">{models.data?.production ?? "ninguno (solo heurística)"}</b> · peso en la puntuación {Math.round((models.data?.blend_alpha ?? 0) * 100)}%</div>
      <button className="btn w-full justify-center" onClick={() => train.mutate()} disabled={train.isPending}>{train.isPending ? <Spinner /> : <BrainCircuit size={15} />} Entrenar y validar</button>
      {r && <div className="rounded-lg bg-panel-2 p-2.5 text-xs text-ink-2 ring-1 ring-line">
        {r.status === "insufficient_data" ? `Datos insuficientes: ${r.n} ejemplos (${r.positives} positivos), mínimo ${r.min_samples} con ambas clases.` :
          `${r.status === "promoted" ? "Promovido" : r.status === "candidate" ? "Candidato (no promovido)" : "Rechazado"} ${r.version} · AUC ${r.metrics?.auc?.toFixed(3)} vs ${r.metrics?.baseline_auc?.toFixed?.(3) ?? "—"} · ${r.n} ejemplos`}
      </div>}
      <div className="space-y-1.5">
        {models.data?.items.slice(0, 6).map((m) => (
          <div key={m.id} className="flex items-center gap-2 rounded-lg bg-panel-2 px-2.5 py-2 text-xs ring-1 ring-line">
            {m.status === "production" ? <CheckCircle2 size={13} className="text-emerald-400" /> : <span className="h-3 w-3" />}
            <span className="font-medium">{m.version}</span>
            <span className="text-ink-3">{m.status} · n={m.n_samples} · AUC {m.metrics.auc?.toFixed?.(3) ?? "—"}</span>
            {m.status === "candidate" && <button className="btn ml-auto px-2 py-0.5 text-[11px]" onClick={() => promote.mutate(m.id)}>Promover</button>}
          </div>
        ))}
      </div>
    </section>
  );
}
