import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BarChart3, BookOpenCheck, Clapperboard, LayoutDashboard, Library, Menu, Plus, Radio, Scissors, Settings as SettingsIcon, Sparkles, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import AddStreamModal from "./components/AddStreamModal";
import { api } from "./lib/api";
import { useEvents } from "./lib/events";
import type { SystemStatus } from "./lib/types";
import Analytics from "./pages/Analytics";
import Dashboard from "./pages/Dashboard";
import Detected from "./pages/Detected";
import Editor from "./pages/Editor";
import LibraryPage from "./pages/Library";
import LiveStreams from "./pages/LiveStreams";
import Settings from "./pages/Settings";
import Training from "./pages/Training";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/live", label: "Live Streams", icon: Radio },
  { to: "/detected", label: "Clips detectados", icon: Sparkles, badge: "pending_review" },
  { to: "/library", label: "Biblioteca", icon: Library },
  { to: "/training", label: "Training Library", icon: BookOpenCheck },
  { to: "/editor", label: "Editor", icon: Scissors },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/settings", label: "Configuración", icon: SettingsIcon },
];

export default function App() {
  const [adding, setAdding] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const { connected } = useEvents();
  const status = useQuery({ queryKey: ["status"], queryFn: () => api.get<SystemStatus>("/api/system/status"), refetchInterval: 3000 });
  const counts = status.data?.counts ?? {};

  return (
    <div className="flex h-full">
      <aside className={clsx("fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-line bg-panel transition-transform lg:static lg:translate-x-0",
        navOpen ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-16 items-center gap-2.5 px-5">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 shadow-lg shadow-violet-900/40">
            <Clapperboard size={17} className="text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-bold tracking-tight">Live Clipper</div>
            <div className="text-[10px] font-medium uppercase tracking-wider text-ink-3">Universal AI</div>
          </div>
        </div>
        <nav className="space-y-0.5 px-3 py-2">
          {NAV.map(({ to, label, icon: Icon, end, badge }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setNavOpen(false)}
              className={({ isActive }) => clsx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                isActive ? "bg-accent-soft text-ink ring-1 ring-accent/40" : "text-ink-2 hover:bg-panel-3 hover:text-ink")}>
              <Icon size={17} />
              <span className="flex-1">{label}</span>
              {badge && counts[badge] ? <span className="tabular rounded-full bg-accent px-1.5 text-[10px] font-bold text-white">{counts[badge]}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="absolute inset-x-3 bottom-4 rounded-xl bg-panel-2 p-3 text-[11px] text-ink-3 ring-1 ring-line">
          <div className="flex items-center gap-1.5">
            {connected ? <Wifi size={13} className="text-emerald-400" /> : <WifiOff size={13} className="text-rose-400" />}
            <span>{connected ? "Eventos en tiempo real" : "Reconectando…"}</span>
          </div>
          <div className="mt-1">ASR: {status.data?.asr.model ?? "—"} · {status.data?.asr.device ?? "pendiente"}</div>
          <div>LLM: {status.data?.llm.provider === "none" ? "heurístico" : status.data?.llm.model}</div>
        </div>
      </aside>
      {navOpen && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setNavOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur md:px-8">
          <button className="btn btn-ghost p-2 lg:hidden" onClick={() => setNavOpen(true)} aria-label="Menú"><Menu size={18} /></button>
          <div className="hidden text-xs text-ink-3 md:block">
            {counts.monitoring ?? 0} canales monitorizados · {Object.keys(status.data?.sessions ?? {}).length} procesando ahora · {counts.clips_rendering ?? 0} renders en curso
          </div>
          <div className="flex-1" />
          <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Añadir transmisión</button>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <Routes>
            <Route path="/" element={<Dashboard onAdd={() => setAdding(true)} />} />
            <Route path="/live" element={<LiveStreams onAdd={() => setAdding(true)} />} />
            <Route path="/detected" element={<Detected />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/training" element={<Training />} />
            <Route path="/editor" element={<Editor />} />
            <Route path="/editor/:clipId" element={<Editor />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
      <AddStreamModal open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}
