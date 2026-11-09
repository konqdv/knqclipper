export const CRITERIA_LABELS: Record<string, string> = {
  hook: "Gancho",
  entertainment: "Entretenimiento",
  narrative: "Narrativa",
  emotion: "Emoción",
  flow: "Fluidez",
  shareability: "Compartible",
  audio_quality: "Audio",
};

export const CATEGORY_LABELS: Record<string, string> = {
  entertainment: "Entretenimiento",
  podcast: "Podcast / entrevista",
  debate: "Debate",
  irl: "IRL / vlog",
  gaming: "Gaming",
  education: "Educación",
  news: "Noticias",
  sports: "Deportes",
  cooking: "Cocina",
  music: "Música",
  beauty: "Belleza",
  tech: "Tecnología",
  motivational: "Motivación",
  general: "General",
};

export const TYPE_LABELS: Record<string, string> = {
  humor: "Humor",
  reaction: "Reacción",
  story: "Historia",
  revelation: "Revelación",
  controversy: "Polémica",
  opinion: "Opinión",
  conflict: "Conflicto",
  tension: "Tensión",
  useful_tip: "Consejo útil",
  surprising_fact: "Dato sorprendente",
  play: "Jugada",
  fail: "Fail",
  interaction: "Interacción",
  question: "Pregunta",
  conversation: "Conversación",
  absurd: "Absurdo",
  challenge: "Reto",
  confession: "Confesión",
};

export const PRIORITY: Record<string, { label: string; cls: string }> = {
  excellent: { label: "Excelente", cls: "bg-emerald-500/15 text-emerald-300 ring-emerald-400/30" },
  high: { label: "Alta prioridad", cls: "bg-violet-500/15 text-violet-200 ring-violet-400/30" },
  review: { label: "Revisar", cls: "bg-amber-500/15 text-amber-200 ring-amber-400/30" },
  low: { label: "Baja", cls: "bg-slate-500/15 text-slate-300 ring-slate-400/25" },
};

export function catLabel(c?: string | null) {
  return c ? CATEGORY_LABELS[c] ?? c : "—";
}

export function fmtDuration(s?: number | null) {
  if (s == null || isNaN(s)) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m ? `${m}:${String(sec).padStart(2, "0")}` : `${sec}s`;
}

export function fmtUptime(s?: number | null) {
  if (!s) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}

export function fmtDate(ts?: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("es", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function compact(n?: number | null) {
  if (n == null) return "—";
  return Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function timeAgo(ts?: number | null) {
  if (!ts) return "—";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "ahora";
  if (d < 3600) return `hace ${Math.floor(d / 60)} min`;
  if (d < 86400) return `hace ${Math.floor(d / 3600)} h`;
  return `hace ${Math.floor(d / 86400)} d`;
}
