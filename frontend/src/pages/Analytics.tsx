import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, FileUp, Plus, RefreshCw, Table2, Trash2 } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Chip, Empty, Field, Modal, PageHeader, Spinner, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { catLabel, compact, fmtDate, TYPE_LABELS } from "../lib/format";
import type { ClipSummary, InsightRow, Paged, Publication } from "../lib/types";

type Metric = "performance" | "avg_retention" | "avg_completion" | "avg_views" | "engagement";
const METRICS: Record<Metric, { label: string; fmt: (v: number) => string }> = {
  performance: { label: "Índice de rendimiento", fmt: (v) => v.toFixed(2) },
  avg_retention: { label: "Retención media %", fmt: (v) => `${v.toFixed(0)}%` },
  avg_completion: { label: "Finalización %", fmt: (v) => `${v.toFixed(0)}%` },
  avg_views: { label: "Visualizaciones medias", fmt: (v) => compact(v) },
  engagement: { label: "Interacción por vista", fmt: (v) => `${(v * 100).toFixed(1)}%` },
};
const GROUPS: { key: string; label: string; map?: (g: string) => string }[] = [
  { key: "by_duration", label: "Duración" },
  { key: "by_hook_strength", label: "Fuerza del gancho" },
  { key: "by_moment_type", label: "Tipo de momento", map: (g) => TYPE_LABELS[g] ?? g },
  { key: "by_category", label: "Categoría", map: catLabel },
  { key: "by_caption_style", label: "Estilo de captions" },
  { key: "by_pacing", label: "Ritmo de edición" },
  { key: "by_platform", label: "Plataforma" },
];
const PLAT: Record<string, string> = { tiktok: "TikTok", reels: "Instagram Reels", feed: "Instagram", shorts: "YouTube Shorts", fb_reels: "Facebook Reels" };
const PUB_STATUS: Record<string, string> = { queued: "en cola", uploading: "subiendo", published: "publicado", draft: "borrador", failed: "falló", registered: "" };

export default function Analytics() {
  const [tab, setTab] = useState<"social" | "learning">("social");
  return (
    <div>
      <PageHeader title="Analytics" subtitle="Rendimiento real de las publicaciones y lo que el sistema ha aprendido de tus decisiones." />
      <div className="mb-5"><Tabs value={tab} onChange={setTab} tabs={[{ id: "social", label: "Social Performance" }, { id: "learning", label: "Aprendizaje editorial" }]} /></div>
      {tab === "social" ? <Social /> : <Learning />}
    </div>
  );
}

function Social() {
  const qc = useQueryClient();
  const insights = useQuery({ queryKey: ["insights"], queryFn: () => api.get<Record<string, any>>("/api/social/insights") });
  const pubs = useQuery({ queryKey: ["publications"], queryFn: () => api.get<Publication[]>("/api/social/publications") });
  const [metric, setMetric] = useState<Metric>("performance");
  const [adding, setAdding] = useState(false);
  const [metricsFor, setMetricsFor] = useState<Publication | null>(null);
  const [csv, setCsv] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["insights"] }); qc.invalidateQueries({ queryKey: ["publications"] }); };
  const sync = useMutation({ mutationFn: (id: number) => api.post(`/api/social/publications/${id}/sync`), onSettled: refresh });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/social/publications/${id}`), onSettled: refresh });
  const ins = insights.data;
  const evp = ins?.editorial_vs_performance;

  return (
    <div className="space-y-6">
      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold">Publicaciones</h2>
          <div className="flex gap-2">
            <button className="btn" onClick={() => setCsv(true)}><FileUp size={15} /> Importar CSV</button>
            <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={15} /> Registrar publicación</button>
          </div>
        </div>
        {pubs.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wider text-ink-3"><tr>
                <th className="px-5 py-2.5 font-medium">Clip</th><th className="px-3 font-medium">Plataforma</th><th className="px-3 text-right font-medium">Vistas</th>
                <th className="px-3 text-right font-medium">Retención</th><th className="px-3 text-right font-medium">Finalización</th><th className="px-3 text-right font-medium">Compartidos</th>
                <th className="px-3 text-right font-medium">Guardados</th><th className="px-3 text-right font-medium">Seguidores</th><th className="px-3 font-medium">Actualizado</th><th /></tr></thead>
              <tbody>{pubs.data.map((p) => (
                <tr key={p.id} className="border-t border-line/70">
                  <td className="max-w-[220px] truncate px-5 py-2.5">{p.url ? <a href={p.url} target="_blank" rel="noreferrer" className="hover:underline">{p.clip_title}</a> : p.clip_title}</td>
                  <td className="px-3 text-ink-2">{PLAT[p.platform] ?? p.platform}{p.status && PUB_STATUS[p.status] ? <span className={`ml-1 text-[10px] ${p.status === "failed" ? "text-rose-300" : p.status === "published" || p.status === "draft" ? "text-emerald-300" : "text-ink-3"}`}>· {PUB_STATUS[p.status]}</span> : null}</td>
                  <td className="tabular px-3 text-right">{compact(p.latest?.views)}</td>
                  <td className="tabular px-3 text-right">{p.latest?.avg_retention != null ? `${p.latest.avg_retention.toFixed(0)}%` : "—"}</td>
                  <td className="tabular px-3 text-right">{p.latest?.completion_rate != null ? `${p.latest.completion_rate.toFixed(0)}%` : "—"}</td>
                  <td className="tabular px-3 text-right">{compact(p.latest?.shares)}</td>
                  <td className="tabular px-3 text-right">{compact(p.latest?.saves)}</td>
                  <td className="tabular px-3 text-right">{compact(p.latest?.followers_gained)}</td>
                  <td className="px-3 text-xs text-ink-3">{p.latest ? `${fmtDate(p.latest.captured_at as number)} · ${p.latest.source}` : "sin métricas"}</td>
                  <td className="whitespace-nowrap px-3 text-right">
                    <button className="btn btn-ghost px-2 py-1 text-xs" onClick={() => setMetricsFor(p)}>Métricas</button>
                    {p.platform === "shorts" && <button className="btn btn-ghost p-1.5" title="Sincronizar con YouTube Analytics" onClick={() => sync.mutate(p.id)}><RefreshCw size={13} /></button>}
                    <button className="btn btn-ghost btn-danger p-1.5" onClick={() => del.mutate(p.id)}><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
            {sync.error && <div className="px-5 pb-3 text-xs text-rose-300">{(sync.error as Error).message}</div>}
          </div>
        ) : <div className="p-5 text-sm text-ink-3">Registra dónde publicaste cada clip y sus métricas (manual, CSV o YouTube Analytics para Shorts).</div>}
      </section>

      {ins && ins.publications > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="label">Métrica</span>
            {(Object.keys(METRICS) as Metric[]).map((m) => <Chip key={m} active={metric === m} onClick={() => setMetric(m)}>{METRICS[m].label}</Chip>)}
          </div>
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {GROUPS.map((g) => <InsightChart key={g.key} title={g.label} rows={(ins[g.key] as InsightRow[]) ?? []} metric={metric} mapLabel={g.map} />)}
          </div>
          <section className="card p-5">
            <h2 className="mb-1 text-sm font-semibold">Calidad editorial vs. rendimiento</h2>
            <p className="mb-3 text-xs text-ink-3">{ins.note}</p>
            <div className="text-sm text-ink-2">Clips con calificación y métricas: <b className="text-ink">{evp?.rated ?? 0}</b>
              {evp?.correlation != null && <> · correlación calificación↔rendimiento <b className="text-ink">{evp.correlation}</b></>}</div>
            {evp?.disagreements?.length ? (
              <ul className="mt-3 space-y-1.5 text-sm">{evp.disagreements.map((d: any) => (
                <li key={`${d.clip_id}-${d.platform}`} className="rounded-lg bg-panel-2 px-3 py-2 ring-1 ring-line"><b>{d.title}</b> <span className="text-ink-3">({PLAT[d.platform]}, {d.rating}/5)</span> — {d.kind}</li>
              ))}</ul>
            ) : <div className="mt-2 text-xs text-ink-3">Sin discrepancias destacables (se necesitan ≥ 5 clips calificados con métricas).</div>}
          </section>
        </>
      ) : <Empty icon={<BarChart3 />} title="Aún no hay métricas de publicaciones">Los insights aparecen cuando registras resultados de TikTok, Reels o Shorts.</Empty>}

      <AddPublication open={adding} onClose={() => { setAdding(false); refresh(); }} />
      {metricsFor && <MetricsForm pub={metricsFor} onClose={() => { setMetricsFor(null); refresh(); }} />}
      <CsvImport open={csv} onClose={() => { setCsv(false); refresh(); }} />
    </div>
  );
}

/** Single-series horizontal bars: one hue, value at the tip, hover tooltip with sample size, table toggle. */
function InsightChart({ title, rows, metric, mapLabel }: { title: string; rows: InsightRow[]; metric: Metric; mapLabel?: (g: string) => string }) {
  const [table, setTable] = useState(false);
  const data = rows.filter((r) => r[metric] != null).map((r) => ({ ...r, label: mapLabel ? mapLabel(r.group) : r.group, value: r[metric] as number }))
    .sort((a, b) => b.value - a.value);
  const fmt = METRICS[metric].fmt;
  return (
    <section className="rounded-[14px] bg-panel-2 p-4 ring-1 ring-line">
      <div className="mb-2 flex items-center justify-between">
        <div><h3 className="text-sm font-semibold">{title}</h3><div className="text-[11px] text-ink-3">{METRICS[metric].label}</div></div>
        <button className="btn btn-ghost p-1.5" title={table ? "Ver gráfico" : "Ver tabla"} onClick={() => setTable(!table)}>{table ? <BarChart3 size={14} /> : <Table2 size={14} />}</button>
      </div>
      {!data.length ? <div className="py-8 text-center text-xs text-ink-3">Sin datos</div> : table ? (
        <table className="w-full text-xs"><thead className="text-ink-3"><tr><th className="py-1 text-left font-medium">Grupo</th><th className="text-right font-medium">n</th><th className="text-right font-medium">Valor</th></tr></thead>
          <tbody>{data.map((d) => <tr key={d.group} className="border-t border-line/60"><td className="py-1.5 text-ink-2">{d.label}</td><td className="tabular text-right text-ink-3">{d.n}</td><td className="tabular text-right">{fmt(d.value)}</td></tr>)}</tbody></table>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(120, data.length * 34 + 20)}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
            <CartesianGrid horizontal={false} stroke="#232838" />
            <XAxis type="number" hide domain={[0, "dataMax"]} />
            <YAxis type="category" dataKey="label" width={118} tick={{ fill: "#a3a9b8", fontSize: 11 }} axisLine={{ stroke: "#2e3448" }} tickLine={false} />
            <Tooltip cursor={{ fill: "#ffffff08" }} content={({ active, payload }) => active && payload?.length ? (
              <div className="rounded-lg bg-panel px-3 py-2 text-xs shadow-xl ring-1 ring-line-2">
                <div className="font-semibold text-ink">{(payload[0].payload as any).label}</div>
                <div className="text-ink-2">{METRICS[metric].label}: <b className="text-ink">{fmt(payload[0].value as number)}</b></div>
                <div className="text-ink-3">n = {(payload[0].payload as any).n} publicaciones</div>
              </div>) : null} />
            <Bar dataKey="value" fill="#3987e5" barSize={18} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList dataKey="value" position="right" formatter={(v: number) => fmt(v)} style={{ fill: "#a3a9b8", fontSize: 11 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

function AddPublication({ open, onClose }: { open: boolean; onClose: () => void }) {
  const clips = useQuery({ queryKey: ["clips", "ready"], queryFn: () => api.get<Paged<ClipSummary>>("/api/clips?status=ready&limit=200"), enabled: open });
  const [f, setF] = useState({ clip_id: "", platform: "tiktok", url: "" });
  const add = useMutation({ mutationFn: () => api.post("/api/social/publications", { clip_id: +f.clip_id, platform: f.platform, url: f.url || null }), onSuccess: onClose });
  return (
    <Modal open={open} onClose={onClose} title="Registrar publicación">
      <div className="space-y-3">
        <Field label="Clip"><select className="input" value={f.clip_id} onChange={(e) => setF({ ...f, clip_id: e.target.value })}><option value="">Selecciona…</option>{clips.data?.items.map((c) => <option key={c.id} value={c.id}>#{c.id} · {c.title}</option>)}</select></Field>
        <Field label="Plataforma"><div className="flex gap-1.5">{Object.entries(PLAT).map(([k, v]) => <Chip key={k} active={f.platform === k} onClick={() => setF({ ...f, platform: k })}>{v}</Chip>)}</div></Field>
        <Field label="URL de la publicación" hint="Para Shorts permite sincronizar métricas con YouTube Analytics."><input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
        {add.error && <div className="text-xs text-rose-300">{(add.error as Error).message}</div>}
        <div className="flex justify-end"><button className="btn btn-primary" disabled={!f.clip_id || add.isPending} onClick={() => add.mutate()}>Guardar</button></div>
      </div>
    </Modal>
  );
}

function MetricsForm({ pub, onClose }: { pub: Publication; onClose: () => void }) {
  const fields: [string, string][] = [["views", "Visualizaciones"], ["avg_retention", "Retención media (%)"], ["completion_rate", "Finalización (%)"], ["watch_time_s", "Tiempo de reproducción (s)"],
    ["likes", "Me gusta"], ["shares", "Compartidos"], ["comments", "Comentarios"], ["saves", "Guardados"], ["followers_gained", "Seguidores obtenidos"]];
  const [v, setV] = useState<Record<string, string>>(Object.fromEntries(fields.map(([k]) => [k, pub.latest?.[k] != null ? String(pub.latest[k]) : ""])));
  const save = useMutation({
    mutationFn: () => api.post(`/api/social/publications/${pub.id}/metrics`, Object.fromEntries(Object.entries(v).filter(([, x]) => x !== "").map(([k, x]) => [k, +x]))),
    onSuccess: onClose,
  });
  return (
    <Modal open onClose={onClose} title={`Métricas · ${PLAT[pub.platform]}`}>
      <div className="grid grid-cols-2 gap-3">
        {fields.map(([k, l]) => <Field key={k} label={l}><input className="input" type="number" min={0} value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></Field>)}
      </div>
      {save.error && <div className="mt-2 text-xs text-rose-300">{(save.error as Error).message}</div>}
      <div className="mt-4 flex justify-end"><button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Guardar instantánea</button></div>
    </Modal>
  );
}

function CsvImport({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState("clip_id,platform,url,views,avg_retention,completion_rate,watch_time_s,likes,shares,comments,saves,followers_gained\n");
  const imp = useMutation({ mutationFn: () => api.post<{ imported: number; rows: number }>("/api/social/import", { csv: text }) });
  return (
    <Modal open={open} onClose={onClose} title="Importar métricas (CSV)">
      <p className="mb-2 text-xs text-ink-3">Plataformas: tiktok, reels, shorts. Útil para exportaciones de TikTok Studio o Instagram Insights.</p>
      <textarea className="input h-48 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} />
      {imp.data && <div className="mt-2 text-xs text-emerald-300">Importadas {imp.data.imported} de {imp.data.rows} filas.</div>}
      {imp.error && <div className="mt-2 text-xs text-rose-300">{(imp.error as Error).message}</div>}
      <div className="mt-3 flex justify-end gap-2"><button className="btn" onClick={onClose}>Cerrar</button><button className="btn btn-primary" onClick={() => imp.mutate()} disabled={imp.isPending}>Importar</button></div>
    </Modal>
  );
}

function Learning() {
  const p = useQuery({ queryKey: ["patterns"], queryFn: () => api.get<Record<string, any>>("/api/learning/patterns") });
  if (p.isLoading) return <Spinner />;
  const d = p.data ?? {};
  const label: Record<string, string> = { duration: "Duración (s)", sub_hook: "Gancho", sub_entertainment: "Entretenimiento", sub_narrative: "Narrativa", sub_emotion: "Emoción",
    sub_flow: "Fluidez", sub_shareability: "Compartible", words_per_second: "Palabras/s", chat_burst_z: "Ráfaga de chat (z)", story_score: "Estructura narrativa" };
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <section className="card p-5">
        <h2 className="mb-1 text-sm font-semibold">Aprobados vs. rechazados</h2>
        <p className="mb-3 text-xs text-ink-3">{d.n_approved ?? 0} aprobados · {d.n_rejected ?? 0} rechazados (feedback + Training Library). Modelo: {d.model_version ?? "heurístico"}</p>
        <table className="w-full text-sm"><thead className="text-left text-[11px] uppercase tracking-wider text-ink-3"><tr><th className="py-1.5 font-medium">Rasgo</th><th className="text-right font-medium">Aprobados</th><th className="text-right font-medium">Rechazados</th></tr></thead>
          <tbody>{(d.features ?? []).map((f: any) => (
            <tr key={f.feature} className="border-t border-line/70"><td className="py-2 text-ink-2">{label[f.feature] ?? f.feature}</td>
              <td className="tabular text-right">{f.approved?.toFixed?.(2) ?? "—"}</td><td className="tabular text-right">{f.rejected?.toFixed?.(2) ?? "—"}</td></tr>
          ))}</tbody></table>
      </section>
      <section className="space-y-6">
        <div className="card p-5">
          <h2 className="mb-3 text-sm font-semibold">Motivos más frecuentes</h2>
          <div className="grid grid-cols-2 gap-4 text-sm">
            {(["approve", "reject"] as const).map((k) => (
              <div key={k}><div className="label mb-1.5">{k === "approve" ? "Al aprobar" : "Al rechazar"}</div>
                {Object.entries(d.reasons?.[k] ?? {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 8).map(([r, n]) => (
                  <div key={r} className="flex justify-between border-t border-line/60 py-1 text-ink-2"><span>{r.replace(/_/g, " ")}</span><span className="tabular text-ink-3">{n as number}</span></div>
                )) || null}
                {!Object.keys(d.reasons?.[k] ?? {}).length && <div className="text-xs text-ink-3">—</div>}
              </div>
            ))}
          </div>
        </div>
        <div className="card p-5">
          <h2 className="mb-3 text-sm font-semibold">Preferencias de duración aprendidas</h2>
          {Object.keys(d.duration_preferences ?? {}).length ? (
            <table className="w-full text-sm"><tbody>{Object.entries(d.duration_preferences).map(([k, v]: any) => {
              const [creator, cat] = k.split("|");
              return <tr key={k} className="border-t border-line/60"><td className="py-1.5 text-ink-2">{creator === "*" ? "Todos" : creator} · {cat === "*" ? "todas las categorías" : catLabel(cat)}</td>
                <td className="tabular text-right">{v.lo}–{v.hi} s</td><td className="tabular text-right text-ink-3">n={v.n}</td></tr>;
            })}</tbody></table>
          ) : <div className="text-xs text-ink-3">Se necesitan al menos 5 clips aprobados por creador/categoría.</div>}
        </div>
        {d.top_features?.length ? (
          <div className="card p-5">
            <h2 className="mb-3 text-sm font-semibold">Rasgos con más peso en el modelo</h2>
            {d.top_features.map((f: any) => <div key={f.feature} className="flex justify-between border-t border-line/60 py-1 text-sm text-ink-2"><span>{f.feature}</span><span className="tabular">{f.coef > 0 ? "+" : ""}{f.coef}</span></div>)}
          </div>
        ) : null}
      </section>
    </div>
  );
}
