import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleSlash, Clapperboard, Gamepad2, HardDrive, Loader2, Radio, Tv, X, Youtube } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { CRITERIA_LABELS, PRIORITY } from "../lib/format";
import type { Criterion, Platform } from "../lib/types";

const PLATFORM: Record<Platform, { label: string; icon: ReactNode; dot: string }> = {
  twitch: { label: "Twitch", icon: <Tv size={13} />, dot: "bg-twitch" },
  youtube: { label: "YouTube", icon: <Youtube size={13} />, dot: "bg-youtube" },
  kick: { label: "Kick", icon: <Gamepad2 size={13} />, dot: "bg-kick" },
  local: { label: "Archivo", icon: <HardDrive size={13} />, dot: "bg-slate-400" },
  vod: { label: "VOD", icon: <Clapperboard size={13} />, dot: "bg-sky-400" },
  ingest: { label: "Ingest", icon: <Radio size={13} />, dot: "bg-cyan-400" },
};

export function PlatformBadge({ platform, className }: { platform: Platform; className?: string }) {
  const p = PLATFORM[platform] ?? PLATFORM.vod;
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full bg-panel-3 px-2 py-0.5 text-[11px] font-semibold text-ink-2 ring-1 ring-line", className)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", p.dot)} />
      {p.icon}
      {p.label}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority: string }) {
  const p = PRIORITY[priority] ?? PRIORITY.low;
  return <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1", p.cls)}>{p.label}</span>;
}

export function StatusPill({ state }: { state: string }) {
  const map: Record<string, { t: string; c: string; icon?: ReactNode }> = {
    live: { t: "Procesando en vivo", c: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30", icon: <span className="live-dot h-2 w-2 rounded-full bg-emerald-400" /> },
    starting: { t: "Conectando", c: "text-sky-300 bg-sky-500/10 ring-sky-500/30", icon: <Loader2 size={12} className="animate-spin" /> },
    finishing: { t: "Finalizando análisis", c: "text-amber-200 bg-amber-500/10 ring-amber-500/30", icon: <Loader2 size={12} className="animate-spin" /> },
    waiting: { t: "Esperando directo", c: "text-ink-2 bg-panel-3 ring-line", icon: <Radio size={12} /> },
    offline: { t: "Sin monitorizar", c: "text-ink-3 bg-panel-3 ring-line", icon: <CircleSlash size={12} /> },
    error: { t: "Error", c: "text-rose-300 bg-rose-500/10 ring-rose-500/30", icon: <AlertTriangle size={12} /> },
    ready: { t: "Listo", c: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30", icon: <CheckCircle2 size={12} /> },
    rendering: { t: "Renderizando", c: "text-violet-200 bg-violet-500/10 ring-violet-500/30", icon: <Loader2 size={12} className="animate-spin" /> },
    queued: { t: "En cola", c: "text-sky-300 bg-sky-500/10 ring-sky-500/30", icon: <Loader2 size={12} className="animate-spin" /> },
    failed: { t: "Falló", c: "text-rose-300 bg-rose-500/10 ring-rose-500/30", icon: <AlertTriangle size={12} /> },
    draft: { t: "Borrador", c: "text-ink-2 bg-panel-3 ring-line" },
  };
  const s = map[state] ?? { t: state, c: "text-ink-2 bg-panel-3 ring-line" };
  return <span className={clsx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1", s.c)}>{s.icon}{s.t}</span>;
}

/** Circular score 0-100. Text uses ink tokens; the ring carries the value. */
export function ScoreRing({ score, size = 52 }: { score: number; size?: number }) {
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, score));
  const color = v >= 90 ? "#34d399" : v >= 75 ? "#a78bfa" : v >= 60 ? "#fab219" : "#6b7280";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title={`Puntuación ${v.toFixed(1)} / 100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#232838" strokeWidth={4} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth={4} fill="none" strokeLinecap="round" strokeDasharray={`${(v / 100) * c} ${c}`} />
      </svg>
      <span className="tabular absolute inset-0 grid place-items-center text-sm font-bold text-ink">{Math.round(v)}</span>
    </div>
  );
}

/** Horizontal meter. Fill severity: accent → warning → danger when `invert` (resource usage). */
export function Meter({ value, max = 100, invert = false, className }: { value: number; max?: number; invert?: boolean; className?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  let fill = "bg-series-1";
  if (invert) fill = pct > 90 ? "bg-crit" : pct > 75 ? "bg-warn" : "bg-series-1";
  return (
    <div className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-series-1-track", className)}>
      <div className={clsx("h-full rounded-full transition-all duration-500", fill)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SubscoreBars({ subscores, points, weights, compact = false }: {
  subscores: Partial<Record<Criterion, number>>; points?: Partial<Record<Criterion, number>>; weights?: Partial<Record<Criterion, number>>; compact?: boolean;
}) {
  const keys: Criterion[] = ["hook", "entertainment", "narrative", "emotion", "flow", "shareability", "audio_quality"];
  return (
    <div className={clsx("grid gap-x-4", compact ? "grid-cols-2 gap-y-1.5" : "grid-cols-1 gap-y-2")}>
      {keys.map((k) => (
        <div key={k} className="min-w-0">
          <div className="mb-0.5 flex items-baseline justify-between text-[11px]">
            <span className="text-ink-2">{CRITERIA_LABELS[k]}</span>
            <span className="tabular text-ink-3">
              {points?.[k] != null && weights?.[k] != null ? `${points[k]!.toFixed(1)}/${weights[k]!.toFixed(0)}` : `${Math.round((subscores[k] ?? 0) * 100)}%`}
            </span>
          </div>
          <Meter value={(subscores[k] ?? 0) * 100} />
        </div>
      ))}
    </div>
  );
}

export function StatTile({ label, value, hint, icon }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between text-xs font-medium text-ink-2">
        {label}
        <span className="text-ink-3">{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold text-ink">{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-3">{hint}</div>}
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <div className={clsx("card max-h-[92vh] w-full overflow-y-auto bg-panel shadow-2xl", wide ? "max-w-4xl" : "max-w-xl")} onMouseDown={(e) => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-panel px-5 py-3.5">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn btn-ghost p-1.5" onClick={onClose} aria-label="Cerrar"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="card grid place-items-center gap-2 px-6 py-14 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-accent-soft text-accent-2">{icon}</div>
      <div className="text-base font-semibold">{title}</div>
      {children && <div className="max-w-md text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex flex-wrap gap-1 rounded-xl bg-panel-2 p-1 ring-1 ring-line">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={clsx("rounded-lg px-3 py-1.5 text-xs font-semibold transition", value === t.id ? "bg-panel-3 text-ink ring-1 ring-line-2" : "text-ink-2 hover:text-ink")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Chip({ active, onClick, children }: { active?: boolean; onClick?: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className={clsx("rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition",
        active ? "bg-accent-soft text-accent-2 ring-accent/60" : "bg-panel-2 text-ink-2 ring-line hover:text-ink")}>
      {children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-ink-3">{hint}</span>}
    </label>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx("animate-spin text-ink-3", className)} size={18} />;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
