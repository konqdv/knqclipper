import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, KeyRound, Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, PageHeader, Spinner } from "../components/ui";
import { api, getToken, setToken } from "../lib/api";
import { CRITERIA_LABELS } from "../lib/format";

type AppSettings = Record<string, Record<string, any>>;
interface SettingsResp { app: AppSettings; defaults: AppSettings; captions: { presets: Record<string, any>; fonts: string[] }; runtime: Record<string, any> }
interface Cred { provider: string; name: string; configured: boolean; source: string | null; secret: boolean }

export default function Settings() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsResp>("/api/settings") });
  if (!q.data) return <Spinner />;
  return (
    <div className="space-y-6">
      <PageHeader title="Configuración" subtitle="Criterios de puntuación, detección, subtítulos, render, aprendizaje, IA y credenciales." />
      <div className="grid gap-6 xl:grid-cols-2">
        <RankerSection app={q.data.app} />
        <Section title="Detección" section="detection" app={q.data.app} fields={[
          ["scan_interval_s", "Escaneo semántico cada (s)", 10, 300, 5], ["min_duration", "Duración mínima (s)", 5, 60, 1], ["max_duration", "Duración máxima (s)", 20, 180, 5],
          ["min_candidate_score", "Puntuación mínima de candidato", 0, 90, 1], ["dedup_iou", "Solape para duplicado (IoU)", 0.1, 0.9, 0.05], ["dedup_text_similarity", "Similitud de texto duplicado", 0.2, 0.95, 0.05]]} />
        <Section title="Render" section="render" app={q.data.app} fields={[["crf", "Calidad CRF (menor = mejor)", 14, 28, 1], ["fps", "FPS de salida", 24, 60, 1]]}
          extra={(v, set) => (
            <Field label="Preset x264"><select className="input" value={v.preset} onChange={(e) => set({ ...v, preset: e.target.value })}>
              {["veryfast", "faster", "fast", "medium", "slow"].map((p) => <option key={p}>{p}</option>)}</select></Field>)} />
        <Section title="Aprendizaje" section="learning" app={q.data.app} fields={[["min_samples", "Mínimo de ejemplos para usar el modelo", 5, 200, 1], ["blend_k", "Inercia de mezcla (k)", 5, 200, 5],
          ["promote_margin", "Mejora mínima de AUC para promover", 0, 0.1, 0.005], ["performance_weight", "Peso del rendimiento social", 0, 0.5, 0.05]]}
          extra={(v, set) => <label className="flex items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={!!v.auto_promote} onChange={(e) => set({ ...v, auto_promote: e.target.checked })} /> Promover automáticamente si mejora en validación</label>} />
        <CaptionDefaults app={q.data.app} presets={Object.keys(q.data.captions.presets)} fonts={q.data.captions.fonts} />
        <LLMSection runtime={q.data.runtime} />
        <Credentials publicBase={q.data.runtime.public_base_url} />
        <ApiToken required={q.data.runtime.api_token_required} />
      </div>
    </div>
  );
}

function useSave(section: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (value: Record<string, any>) => api.put("/api/settings", { section, value }), onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
}

function RankerSection({ app }: { app: AppSettings }) {
  const [v, setV] = useState(app.ranker);
  const save = useSave("ranker");
  const total = Object.values(v.weights as Record<string, number>).reduce((a, b) => a + b, 0);
  return (
    <section className="card space-y-4 p-5">
      <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Puntuación (Highlight Ranker)</h2><span className="tabular text-xs text-ink-3">Total de pesos: <b className={total === 100 ? "text-emerald-300" : "text-amber-300"}>{total}</b> (se normaliza a 100)</span></div>
      {Object.entries(v.weights as Record<string, number>).map(([k, w]) => (
        <div key={k} className="flex items-center gap-3 text-sm">
          <span className="w-32 text-ink-2">{CRITERIA_LABELS[k] ?? k}</span>
          <input type="range" min={0} max={40} value={w} onChange={(e) => setV({ ...v, weights: { ...v.weights, [k]: +e.target.value } })} />
          <span className="tabular w-8 text-right">{w}</span>
        </div>
      ))}
      <div className="grid grid-cols-3 gap-3 border-t border-line pt-4">
        {[["excellent", "Excelente ≥"], ["high", "Alta ≥"], ["review", "Revisar ≥"]].map(([k, l]) => (
          <Field key={k} label={l}><input className="input" type="number" min={0} max={100} value={v.tiers[k]} onChange={(e) => setV({ ...v, tiers: { ...v.tiers, [k]: +e.target.value } })} /></Field>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={!!v.context_adaptive} onChange={(e) => setV({ ...v, context_adaptive: e.target.checked })} />
        Adaptar pesos al tipo de contenido (p. ej. la educación no se penaliza por no tener risas)</label>
      <SaveBtn m={save} onClick={() => save.mutate(v)} />
    </section>
  );
}

function Section({ title, section, app, fields, extra }: {
  title: string; section: string; app: AppSettings; fields: [string, string, number, number, number][];
  extra?: (v: Record<string, any>, set: (v: Record<string, any>) => void) => React.ReactNode;
}) {
  const [v, setV] = useState(app[section]);
  const save = useSave(section);
  return (
    <section className="card space-y-4 p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map(([k, l, min, max, step]) => (
          <Field key={k} label={`${l}: ${v[k]}`}><input type="range" min={min} max={max} step={step} value={v[k]} onChange={(e) => setV({ ...v, [k]: +e.target.value })} /></Field>
        ))}
      </div>
      {extra?.(v, setV)}
      <SaveBtn m={save} onClick={() => save.mutate(v)} />
    </section>
  );
}

function CaptionDefaults({ app, presets, fonts }: { app: AppSettings; presets: string[]; fonts: string[] }) {
  const [v, setV] = useState(app.captions);
  const save = useSave("captions");
  return (
    <section className="card space-y-4 p-5">
      <h2 className="text-sm font-semibold">Subtítulos por defecto</h2>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Estilo"><select className="input" value={v.style} onChange={(e) => setV({ ...v, style: e.target.value })}>{presets.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Tipografía"><select className="input" value={v.font} onChange={(e) => setV({ ...v, font: e.target.value })}>{fonts.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Color destacado"><input type="color" className="h-9 w-full rounded-lg bg-transparent" value={v.highlight_color} onChange={(e) => setV({ ...v, highlight_color: e.target.value })} /></Field>
      </div>
      <p className="text-xs text-ink-3">El estilo elegido aquí se aplica a los clips nuevos; cada canal y cada clip pueden sobrescribirlo.</p>
      <SaveBtn m={save} onClick={() => save.mutate(v)} />
    </section>
  );
}

function LLMSection({ runtime }: { runtime: Record<string, any> }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ provider: runtime.llm_provider, model: runtime.llm_model, base_url: runtime.llm_base_url ?? "", vision: !!runtime.llm_vision });
  const health = useQuery({ queryKey: ["llm-health"], queryFn: () => api.get<Record<string, any>>("/api/system/llm") });
  const save = useMutation({ mutationFn: () => api.put("/api/settings/llm", v), onSuccess: () => { qc.invalidateQueries({ queryKey: ["llm-health"] }); qc.invalidateQueries({ queryKey: ["status"] }); } });
  return (
    <section className="card space-y-4 p-5">
      <h2 className="text-sm font-semibold">Comprensión contextual (LLM)</h2>
      <p className="text-xs text-ink-3">Opcional. Sin LLM el motor usa análisis heurístico multiseñal. Las citas y límites que devuelve el modelo se validan contra la transcripción.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Proveedor"><select className="input" value={v.provider} onChange={(e) => setV({ ...v, provider: e.target.value })}>
          <option value="none">Ninguno (heurístico)</option><option value="ollama">Ollama (local)</option><option value="openai">Compatible con OpenAI</option><option value="anthropic">Anthropic</option></select></Field>
        <Field label="Modelo"><input className="input" value={v.model} onChange={(e) => setV({ ...v, model: e.target.value })} placeholder="qwen2.5:7b-instruct" /></Field>
        <Field label="URL base" hint="Ollama: http://localhost:11434 · vLLM/LM Studio: http://host:puerto/v1"><input className="input" value={v.base_url} onChange={(e) => setV({ ...v, base_url: e.target.value })} /></Field>
        <label className="mt-6 flex items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={v.vision} onChange={(e) => setV({ ...v, vision: e.target.checked })} /> Enviar fotogramas (modelo multimodal)</label>
      </div>
      <div className="text-xs text-ink-2">Estado: {health.data ? (health.data.provider === "none" ? "heurístico" : health.data.ok === false ? `sin conexión (${health.data.error})` : `${health.data.provider} · ${health.data.model}${health.data.available === false ? " (modelo no descargado)" : ""}`) : "…"}</div>
      <SaveBtn m={save} onClick={() => save.mutate()} />
    </section>
  );
}

const CRED_LABELS: Record<string, string> = {
  "twitch/client_id": "Twitch Client ID", "twitch/client_secret": "Twitch Client Secret", "kick/client_id": "Kick Client ID", "kick/client_secret": "Kick Client Secret",
  "youtube/api_key": "YouTube Data API key", "google/client_id": "Google OAuth Client ID", "google/client_secret": "Google OAuth Client Secret", "llm/api_key": "API key del proveedor LLM",
  "google/refresh_token": "Cuenta de Google conectada",
};

function Credentials({ publicBase }: { publicBase: string | null }) {
  const qc = useQueryClient();
  const creds = useQuery({ queryKey: ["credentials"], queryFn: () => api.get<Cred[]>("/api/credentials") });
  const [vals, setVals] = useState<Record<string, string>>({});
  const save = useMutation({ mutationFn: (c: { provider: string; name: string; value: string }) => api.put("/api/credentials", c), onSuccess: () => { setVals({}); qc.invalidateQueries({ queryKey: ["credentials"] }); } });
  const del = useMutation({ mutationFn: (c: Cred) => api.del(`/api/credentials/${c.provider}/${c.name}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }) });
  return (
    <section className="card space-y-3 p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><KeyRound size={15} /> Credenciales y APIs oficiales</h2>
      <p className="text-xs text-ink-3">Se cifran en la base de datos (Fernet) y nunca se devuelven. Las variables de entorno CLIPPER_* tienen prioridad.</p>
      {creds.data?.map((c) => {
        const k = `${c.provider}/${c.name}`;
        return (
          <div key={k} className="flex items-center gap-2">
            <span className="w-48 shrink-0 text-xs text-ink-2">{CRED_LABELS[k] ?? k}</span>
            {c.source === "oauth" ? <span className="flex-1 text-xs text-emerald-300">conectada</span> : (
              <input className="input py-1.5" type={c.secret ? "password" : "text"} placeholder={c.configured ? `configurado (${c.source})` : "no configurado"}
                value={vals[k] ?? ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} disabled={c.source === "env"} />
            )}
            {c.configured && <CheckCircle2 size={15} className="shrink-0 text-emerald-400" />}
            {vals[k] ? <button className="btn px-2 py-1 text-xs" onClick={() => save.mutate({ provider: c.provider, name: c.name, value: vals[k] })}>Guardar</button>
              : c.source === "stored" || c.source === "oauth" ? <button className="btn btn-ghost btn-danger px-2 py-1 text-xs" onClick={() => del.mutate(c)}>Quitar</button> : null}
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3 text-xs text-ink-3">
        <a className="btn" href="/api/oauth/google/start" target="_blank" rel="noreferrer"><ShieldCheck size={15} /> Conectar cuenta de Google (YouTube Analytics)</a>
        <span>Webhooks de Kick y OAuth usan {publicBase ?? "la URL actual"}{publicBase ? "" : " (configura CLIPPER_PUBLIC_BASE_URL para recibir webhooks)"}</span>
      </div>
    </section>
  );
}

function ApiToken({ required }: { required: boolean }) {
  const [t, setT] = useState(getToken() ?? "");
  const [saved, setSaved] = useState(false);
  useEffect(() => setSaved(false), [t]);
  return (
    <section className="card space-y-3 p-5">
      <h2 className="text-sm font-semibold">Acceso a la API</h2>
      <p className="text-xs text-ink-3">{required ? "El servidor exige token (CLIPPER_API_TOKEN). Introdúcelo para este navegador." : "El servidor no exige token (escucha solo en local por defecto)."}</p>
      <div className="flex gap-2"><input className="input" type="password" value={t} onChange={(e) => setT(e.target.value)} placeholder="token" />
        <button className="btn" onClick={() => { setToken(t || null); setSaved(true); }}>{saved ? <CheckCircle2 size={15} /> : <Save size={15} />} Guardar</button></div>
    </section>
  );
}

function SaveBtn({ m, onClick }: { m: { isPending: boolean; isSuccess: boolean; error: unknown }; onClick: () => void }) {
  return (
    <div className="flex items-center justify-end gap-3">
      {m.error ? <span className="text-xs text-rose-300">{(m.error as Error).message}</span> : m.isSuccess ? <span className="text-xs text-emerald-300">Guardado</span> : null}
      <button className="btn btn-primary" onClick={onClick} disabled={m.isPending}>{m.isPending ? <Spinner /> : <Save size={15} />} Guardar</button>
    </div>
  );
}
