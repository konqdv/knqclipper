import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crop, Film, Loader2, Pause, Play, Save, Scissors, Sparkles, Star, Trash2, Type, WandSparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CaptionOverlay, PRESETS, resolveStyle } from "../components/CaptionOverlay";
import { FeedbackBar } from "../components/FeedbackBar";
import { ReframeCanvas } from "../components/ReframeCanvas";
import { Chip, Empty, Field, Modal, PageHeader, ScoreRing, Spinner, StatusPill, Tabs } from "../components/ui";
import { api, withToken } from "../lib/api";
import { useEvents } from "../lib/events";
import { fmtDuration } from "../lib/format";
import type { CaptionStyle, ClipEdit, ClipFull, ClipSummary, Paged, Word } from "../lib/types";

const FONTS = ["Montserrat Black", "Montserrat ExtraBold", "Anton", "Bebas Neue", "Poppins ExtraBold", "Inter SemiBold", "Inter ExtraBold", "Bangers"];
const SWATCHES = ["#FFE600", "#B266FF", "#39FF14", "#00E5FF", "#FF3B3B", "#FF8A00", "#FF4FD8", "#FFFFFF"];
const STYLE_LABELS: Record<string, string> = { word: "Palabra por palabra", phrase: "Por frases", karaoke: "Karaoke", dynamic: "Resaltado dinámico", podcast: "Podcast", gaming: "Gaming", minimal: "Minimalista" };
const STAGE_W = 324;

export default function Editor() {
  const { clipId } = useParams();
  if (!clipId) return <Picker />;
  return <EditorView id={+clipId} key={clipId} />;
}

function Picker() {
  const clips = useQuery({ queryKey: ["clips", "editor"], queryFn: () => api.get<Paged<ClipSummary>>("/api/clips?limit=60") });
  return (
    <div>
      <PageHeader title="Editor" subtitle="Elige un clip para recortar, corregir subtítulos, cambiar estilos y encuadre con vista previa en tiempo real." />
      {clips.data?.items.length ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-6">
          {clips.data.items.map((c) => (
            <Link key={c.id} to={`/editor/${c.id}`} className="card overflow-hidden transition hover:ring-1 hover:ring-accent/50">
              <div className="aspect-[9/16] bg-black">{c.thumbnail_url && <img src={withToken(c.thumbnail_url)} className="h-full w-full object-cover" alt="" />}</div>
              <div className="p-2.5"><div className="line-clamp-2 text-xs font-medium">{c.title}</div><div className="mt-1"><StatusPill state={c.status} /></div></div>
            </Link>
          ))}
        </div>
      ) : <Empty icon={<Scissors />} title="No hay clips todavía">Desde «Clips detectados» usa «Editar antes» o aprueba un momento.</Empty>}
    </div>
  );
}

function EditorView({ id }: { id: number }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { clipProgress, previewUrls } = useEvents();
  const clip = useQuery({ queryKey: ["clip", id], queryFn: () => api.get<ClipFull>(`/api/clips/${id}`) });
  const [edit, setEdit] = useState<ClipEdit | null>(null);
  const [dirty, setDirty] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState<"captions" | "transcript" | "frame" | "extras" | "review">("captions");
  const [showPreview, setShowPreview] = useState(false);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (clip.data && !edit) setEdit(clip.data.edit);
  }, [clip.data, edit]);

  const update = useCallback((patch: Partial<ClipEdit>) => {
    setEdit((e) => (e ? { ...e, ...patch } : e));
    setDirty(true);
  }, []);
  const setStyle = (p: Partial<CaptionStyle>) => edit && update({ caption_style: { ...edit.caption_style, ...p } });

  const save = useMutation({
    mutationFn: () => api.patch<ClipFull>(`/api/clips/${id}`, { edit }),
    onSuccess: (c) => { setDirty(false); setEdit(c.edit); qc.setQueryData(["clip", id], c); },
  });
  const renderFinal = useMutation({
    mutationFn: async () => { if (dirty) await save.mutateAsync(); return api.post(`/api/clips/${id}/render`); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clip", id] }),
  });
  const preview = useMutation({
    mutationFn: async () => { if (dirty) await save.mutateAsync(); return api.post(`/api/clips/${id}/render?preview=true`); },
    onSuccess: () => setShowPreview(true),
  });
  const plan = useMutation({
    mutationFn: async () => { if (dirty) await save.mutateAsync(); return api.post<{ reframe: ClipEdit["reframe"] }>(`/api/clips/${id}/plan`); },
    onSuccess: (r) => setEdit((e) => (e ? { ...e, reframe: r.reframe } : e)),
  });

  const moment = clip.data?.moment;
  const srcUrl = withToken(clip.data?.source_url);
  const visibleWords = useMemo(() => (edit?.words ?? []).filter((w) => w.e > (edit?.trim_start ?? 0) && w.s < (edit?.trim_end ?? 0)), [edit]);

  if (clip.isLoading || !edit) return <Spinner />;
  if (clip.error) return <Empty icon={<Film />} title="Clip no encontrado" />;
  if (!srcUrl) return <Empty icon={<Film />} title="El medio original ya no está disponible">Los originales de momentos rechazados se eliminan tras el periodo de retención.</Empty>;

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime < edit.trim_start || v.currentTime >= edit.trim_end - 0.05) v.currentTime = edit.trim_start;
      v.play();
      setPlaying(true);
    } else {
      v.pause();
      setPlaying(false);
    }
  };
  const seek = (s: number) => { if (video.current) video.current.currentTime = s; };
  const style = resolveStyle(edit.caption_style);
  const progress = clipProgress[id];
  const c = clip.data!;

  return (
    <div>
      <PageHeader title="Editor" subtitle={c.title}
        actions={<>
          {moment && <ScoreRing score={moment.score} size={40} />}
          <StatusPill state={c.status} />
          <button className="btn" onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? <Spinner /> : <Save size={15} />} Guardar</button>
          <button className="btn" onClick={() => preview.mutate()} disabled={preview.isPending}>{preview.isPending ? <Spinner /> : <Sparkles size={15} />} Vista previa del render</button>
          <button className="btn btn-primary" onClick={() => renderFinal.mutate()} disabled={renderFinal.isPending || c.status === "rendering"}>
            {c.status === "rendering" || c.status === "queued" ? <><Loader2 size={15} className="animate-spin" /> {Math.round((progress ?? c.progress) * 100)}%</> : <><Film size={15} /> Renderizar final</>}
          </button>
        </>} />
      {c.error && <div className="mb-4 rounded-lg bg-rose-500/10 p-3 text-xs text-rose-200 ring-1 ring-rose-500/30">{c.error}</div>}

      <div className="grid gap-6 xl:grid-cols-[auto_1fr]">
        <div className="space-y-3">
          <div className="relative mx-auto" style={{ width: STAGE_W }}>
            <ReframeCanvas ref={video} src={srcUrl} edit={edit} width={STAGE_W} onTime={setT} onMeta={(_, __, d) => setDur(d)} />
            <CaptionOverlay words={visibleWords} t={t} style={edit.caption_style} scale={STAGE_W / 1080} clipStart={edit.trim_start}
              title={edit.title.text} titleDuration={edit.title.duration} watermark={edit.watermark} />
          </div>
          <div className="flex items-center justify-center gap-2">
            <button className="btn" onClick={toggle}>{playing ? <Pause size={15} /> : <Play size={15} />}</button>
            <span className="tabular text-xs text-ink-2">{fmtDuration(Math.max(0, t - edit.trim_start))} / {fmtDuration(edit.trim_end - edit.trim_start)}</span>
          </div>
          {c.video_url && <a className="block text-center text-xs text-accent-2 hover:underline" href={withToken(c.video_url)} target="_blank" rel="noreferrer">Abrir último render</a>}
        </div>

        <div className="min-w-0 space-y-4">
          <Timeline edit={edit} dur={dur || moment?.source_duration || edit.trim_end + 5} t={t} words={edit.words} onTrim={(a, b) => update({ trim_start: a, trim_end: b })} onSeek={seek} />
          <Tabs value={tab} onChange={setTab} tabs={[
            { id: "captions", label: <span className="flex items-center gap-1.5"><Type size={13} />Subtítulos</span> },
            { id: "transcript", label: "Transcripción" },
            { id: "frame", label: <span className="flex items-center gap-1.5"><Crop size={13} />Encuadre</span> },
            { id: "extras", label: "Título y marca de agua" },
            { id: "review", label: <span className="flex items-center gap-1.5"><Star size={13} />Evaluar</span> },
          ]} />

          {tab === "captions" && (
            <div className="card space-y-5 p-5">
              <div>
                <div className="label mb-2">Estilo</div>
                <div className="flex flex-wrap gap-1.5">{Object.keys(PRESETS).map((k) => (
                  <Chip key={k} active={edit.caption_style.style === k} onClick={() => update({ caption_style: { style: k, enabled: true } })}>{STYLE_LABELS[k]}</Chip>
                ))}</div>
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Tipografía"><select className="input" value={style.font} onChange={(e) => setStyle({ font: e.target.value })}>{FONTS.map((f) => <option key={f} style={{ fontFamily: f }}>{f}</option>)}</select></Field>
                <Field label={`Tamaño: ${style.font_size}px`}><input type="range" min={40} max={130} value={style.font_size} onChange={(e) => setStyle({ font_size: +e.target.value })} /></Field>
                <Field label="Animación"><select className="input" value={style.animation} onChange={(e) => setStyle({ animation: e.target.value as CaptionStyle["animation"] })}>
                  <option value="pop">Escala (pop)</option><option value="bounce">Rebote</option><option value="fade">Aparición</option><option value="none">Ninguna</option></select></Field>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Color de palabras destacadas">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {SWATCHES.map((s) => <button key={s} onClick={() => setStyle({ highlight_color: s })} title={s}
                      className={`h-7 w-7 rounded-full ring-2 ${style.highlight_color?.toUpperCase() === s ? "ring-white" : "ring-transparent"}`} style={{ background: s }} />)}
                    <input type="color" value={style.highlight_color} onChange={(e) => setStyle({ highlight_color: e.target.value })} className="h-7 w-9 rounded bg-transparent" />
                  </div>
                </Field>
                <Field label="Color de texto / contorno">
                  <div className="flex items-center gap-2">
                    <input type="color" value={style.primary_color} onChange={(e) => setStyle({ primary_color: e.target.value })} className="h-7 w-9 rounded bg-transparent" />
                    <input type="color" value={style.outline_color} onChange={(e) => setStyle({ outline_color: e.target.value })} className="h-7 w-9 rounded bg-transparent" />
                    <span className="text-xs text-ink-3">Contorno {style.outline}px</span>
                    <input type="range" min={0} max={12} value={style.outline} onChange={(e) => setStyle({ outline: +e.target.value })} className="max-w-28" />
                  </div>
                </Field>
              </div>
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Posición"><select className="input" value={style.position} onChange={(e) => setStyle({ position: e.target.value as CaptionStyle["position"] })}>
                  <option value="lower">Inferior</option><option value="middle">Centro</option><option value="top">Superior</option><option value="custom">Personalizada</option></select></Field>
                {style.position === "custom" ? (
                  <Field label={`Altura: ${Math.round((style.pos_y ?? 0.72) * 100)}%`}><input type="range" min={0.08} max={0.95} step={0.01} value={style.pos_y} onChange={(e) => setStyle({ pos_y: +e.target.value })} /></Field>
                ) : (
                  <Field label={`Margen: ${style.margin_v}px`}><input type="range" min={60} max={900} value={style.margin_v} onChange={(e) => setStyle({ margin_v: +e.target.value })} /></Field>
                )}
                <Field label={`Palabras por línea: ${style.max_words_per_line}`}><input type="range" min={1} max={8} value={style.max_words_per_line} onChange={(e) => setStyle({ max_words_per_line: +e.target.value })} /></Field>
              </div>
              <div className="flex flex-wrap gap-4 text-sm text-ink-2">
                <label className="flex items-center gap-2"><input type="checkbox" checked={!!style.uppercase} onChange={(e) => setStyle({ uppercase: e.target.checked })} /> Mayúsculas</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={style.emphasis === "auto"} onChange={(e) => setStyle({ emphasis: e.target.checked ? "auto" : "none" })} /> Destacar palabras clave</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={!!style.box} onChange={(e) => setStyle({ box: e.target.checked })} /> Caja de fondo</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={style.enabled !== false} onChange={(e) => setStyle({ enabled: e.target.checked })} /> Mostrar subtítulos</label>
              </div>
            </div>
          )}

          {tab === "transcript" && <TranscriptEditor words={edit.words} trim={[edit.trim_start, edit.trim_end]} t={t} onChange={(words) => update({ words })} onSeek={seek} />}

          {tab === "frame" && (
            <div className="card space-y-4 p-5">
              <div className="flex flex-wrap gap-1.5">
                {[["auto", "Automático"], ["track", "Seguir sujeto/hablante"], ["split", "Dividido (webcam/2 personas)"], ["fit", "Encuadre completo + fondo"], ["center", "Centro"], ["manual", "Manual"]].map(([m, l]) => (
                  <Chip key={m} active={edit.reframe.mode === m} onClick={() => update({ reframe: { ...edit.reframe, mode: m, plan: m === "manual" || m === "fit" || m === "center" ? edit.reframe.plan : null } })}>{l}</Chip>
                ))}
              </div>
              {edit.reframe.mode === "manual" ? (
                <div className="grid gap-4 md:grid-cols-3">
                  <Field label={`Horizontal: ${Math.round(edit.reframe.manual.x * 100)}%`}><input type="range" min={0} max={1} step={0.005} value={edit.reframe.manual.x} onChange={(e) => update({ reframe: { ...edit.reframe, manual: { ...edit.reframe.manual, x: +e.target.value } } })} /></Field>
                  <Field label={`Vertical: ${Math.round(edit.reframe.manual.y * 100)}%`}><input type="range" min={0} max={1} step={0.005} value={edit.reframe.manual.y} onChange={(e) => update({ reframe: { ...edit.reframe, manual: { ...edit.reframe.manual, y: +e.target.value } } })} /></Field>
                  <Field label={`Zoom: ${edit.reframe.manual.zoom.toFixed(2)}×`}><input type="range" min={1} max={2.5} step={0.01} value={edit.reframe.manual.zoom} onChange={(e) => update({ reframe: { ...edit.reframe, manual: { ...edit.reframe.manual, zoom: +e.target.value } } })} /></Field>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <button className="btn" onClick={() => plan.mutate()} disabled={plan.isPending}>{plan.isPending ? <Spinner /> : <WandSparkles size={15} />} Analizar encuadre</button>
                  {edit.reframe.plan ? (
                    <span className="text-ink-2">Plan: <b className="text-ink">{edit.reframe.plan.layout}</b> — {edit.reframe.plan.reason}
                      {edit.reframe.plan.alternatives?.length ? <span className="text-ink-3"> · alternativas: {edit.reframe.plan.alternatives.join(", ")}</span> : null}</span>
                  ) : <span className="text-ink-3">La vista previa usa recorte central hasta que se analice el encuadre (también se calcula al renderizar).</span>}
                </div>
              )}
              {plan.error && <div className="text-xs text-rose-300">{(plan.error as Error).message}</div>}
            </div>
          )}

          {tab === "extras" && (
            <div className="card grid gap-5 p-5 md:grid-cols-2">
              <div className="space-y-3">
                <Field label="Título inicial"><input className="input" value={edit.title.text} placeholder="Ej.: No te vas a creer lo que pasó" onChange={(e) => update({ title: { ...edit.title, text: e.target.value } })} /></Field>
                <Field label={`Duración del título: ${edit.title.duration}s`}><input type="range" min={1} max={8} step={0.5} value={edit.title.duration} onChange={(e) => update({ title: { ...edit.title, duration: +e.target.value } })} /></Field>
              </div>
              <div className="space-y-3">
                <Field label="Marca de agua (texto)"><input className="input" value={edit.watermark.text} placeholder="@tucanal" onChange={(e) => update({ watermark: { ...edit.watermark, text: e.target.value } })} /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Posición"><select className="input" value={edit.watermark.position} onChange={(e) => update({ watermark: { ...edit.watermark, position: e.target.value } })}>
                    <option value="top-left">Arriba izq.</option><option value="top-right">Arriba der.</option><option value="bottom-left">Abajo izq.</option><option value="bottom-right">Abajo der.</option></select></Field>
                  <Field label={`Opacidad ${Math.round(edit.watermark.opacity * 100)}%`}><input type="range" min={0.1} max={1} step={0.05} value={edit.watermark.opacity} onChange={(e) => update({ watermark: { ...edit.watermark, opacity: +e.target.value } })} /></Field>
                </div>
              </div>
            </div>
          )}

          {tab === "review" && (
            <div className="card space-y-3 p-5">
              <p className="text-sm text-ink-2">Tu evaluación y las modificaciones que hiciste se guardan junto con las señales originales para ajustar la detección.</p>
              <FeedbackBar clipId={id} onDone={() => nav("/library")} />
            </div>
          )}
        </div>
      </div>

      <Modal open={showPreview} onClose={() => setShowPreview(false)} title="Vista previa del render (baja resolución)">
        {previewUrls[id] ? <video src={withToken(previewUrls[id])} controls autoPlay className="mx-auto aspect-[9/16] max-h-[70vh] rounded-xl bg-black" />
          : <div className="flex items-center gap-2 text-sm text-ink-2"><Spinner /> Renderizando con FFmpeg + libass…</div>}
      </Modal>
    </div>
  );
}

function Timeline({ edit, dur, t, words, onTrim, onSeek }: { edit: ClipEdit; dur: number; t: number; words: Word[]; onTrim: (a: number, b: number) => void; onSeek: (s: number) => void }) {
  const bar = useRef<HTMLDivElement>(null);
  const pct = (s: number) => `${(s / Math.max(dur, 0.1)) * 100}%`;
  const [drag, setDrag] = useState<null | "a" | "b">(null);
  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const r = bar.current!.getBoundingClientRect();
      const s = Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur));
      if (drag === "a") onTrim(Math.min(s, edit.trim_end - 3), edit.trim_end);
      else onTrim(edit.trim_start, Math.max(s, edit.trim_start + 3));
    };
    const up = () => setDrag(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [drag, dur, edit.trim_start, edit.trim_end, onTrim]);

  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between text-xs text-ink-2">
        <span className="flex items-center gap-1.5 font-semibold"><Scissors size={13} /> Recorte</span>
        <span className="tabular">Inicio {edit.trim_start.toFixed(2)}s · Fin {edit.trim_end.toFixed(2)}s · Duración <b className="text-ink">{(edit.trim_end - edit.trim_start).toFixed(1)}s</b></span>
      </div>
      <div ref={bar} className="relative h-14 cursor-pointer rounded-lg bg-panel-2 ring-1 ring-line"
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(((e.clientX - r.left) / r.width) * dur); }}>
        {words.map((w, i) => <div key={i} className="absolute top-5 h-4 rounded-sm bg-ink-3/40" style={{ left: pct(w.s), width: `max(1px, ${pct(w.e - w.s)})` }} />)}
        <div className="absolute inset-y-0 rounded-md bg-accent/15 ring-1 ring-accent/60" style={{ left: pct(edit.trim_start), width: pct(edit.trim_end - edit.trim_start) }} />
        {(["a", "b"] as const).map((h) => (
          <div key={h} onPointerDown={(e) => { e.stopPropagation(); setDrag(h); }} onClick={(e) => e.stopPropagation()}
            className="absolute inset-y-0 w-3 -translate-x-1/2 cursor-ew-resize rounded bg-accent" style={{ left: pct(h === "a" ? edit.trim_start : edit.trim_end) }} />
        ))}
        <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-white" style={{ left: pct(t) }} />
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {[-1, -0.25, 0.25, 1].map((d) => <button key={`a${d}`} className="btn px-2 py-1 text-xs" onClick={() => onTrim(Math.max(0, edit.trim_start + d), edit.trim_end)}>Inicio {d > 0 ? "+" : ""}{d}s</button>)}
        <span className="flex-1" />
        {[-1, -0.25, 0.25, 1].map((d) => <button key={`b${d}`} className="btn px-2 py-1 text-xs" onClick={() => onTrim(edit.trim_start, Math.min(dur, edit.trim_end + d))}>Fin {d > 0 ? "+" : ""}{d}s</button>)}
      </div>
    </div>
  );
}

function TranscriptEditor({ words, trim, t, onChange, onSeek }: { words: Word[]; trim: [number, number]; t: number; onChange: (w: Word[]) => void; onSeek: (s: number) => void }) {
  const [editing, setEditing] = useState<number | null>(null);
  const set = (i: number, patch: Partial<Word>) => onChange(words.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  return (
    <div className="card p-5">
      <p className="mb-3 text-xs text-ink-3">Clic para ir a la palabra · doble clic para corregirla · ★ para destacarla. Los tiempos se conservan; el texto original del momento no se modifica.</p>
      <div className="flex max-h-80 flex-wrap gap-1 overflow-y-auto leading-relaxed">
        {words.map((w, i) => {
          const inside = w.e > trim[0] && w.s < trim[1];
          const current = w.s <= t && t < w.e;
          return editing === i ? (
            <input key={i} autoFocus className="input w-32 py-0.5" defaultValue={w.w}
              onBlur={(e) => { set(i, { w: e.target.value.trim() || w.w }); setEditing(null); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setEditing(null); }} />
          ) : (
            <span key={i} className={`group inline-flex items-center rounded px-1 py-0.5 text-sm ${current ? "bg-accent text-white" : inside ? "text-ink hover:bg-panel-3" : "text-ink-3/60"}`}>
              <button onClick={() => onSeek(w.s)} onDoubleClick={() => setEditing(i)} className={w.emphasis ? "font-bold text-amber-300" : ""}>{w.w}</button>
              <button onClick={() => set(i, { emphasis: !w.emphasis })} className="ml-0.5 hidden text-ink-3 hover:text-amber-300 group-hover:inline" title="Destacar"><Star size={11} /></button>
              <button onClick={() => onChange(words.filter((_, j) => j !== i))} className="hidden text-ink-3 hover:text-rose-300 group-hover:inline" title="Quitar del subtítulo"><Trash2 size={11} /></button>
            </span>
          );
        })}
      </div>
    </div>
  );
}
