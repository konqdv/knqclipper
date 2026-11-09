import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2, Send, Settings as SettingsIcon, Sparkles, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, withToken } from "../lib/api";
import type { ClipSummary, Publication, PublishTargets, SocialCopy } from "../lib/types";
import { Chip, Modal, Spinner } from "./ui";

// publish target key -> which generated-copy block feeds it
const COPY_KEY: Record<string, keyof SocialCopy["descriptions"]> = {
  shorts: "shorts", tiktok: "tiktok", reels: "instagram", feed: "instagram", fb_reels: "facebook",
};
const MISSING_LABEL: Record<string, string> = {
  google_oauth_app: "Configura la app de Google en Configuración",
  tiktok_app: "Configura la app de TikTok en Configuración",
  meta_app: "Configura la app de Meta en Configuración",
  account: "Conecta la cuenta en Configuración",
  public_url: "Instagram necesita que la app esté en un servidor con URL pública",
};
const STATUS_LABEL: Record<string, string> = {
  queued: "En cola", uploading: "Subiendo", published: "Publicado", draft: "En borradores", failed: "Falló", registered: "Registrado",
};

export function PublishModal({ clip, open, onClose }: { clip: ClipSummary; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const targets = useQuery({ queryKey: ["publish-targets"], queryFn: () => api.get<PublishTargets>("/api/publish/targets"), enabled: open });
  const pubs = useQuery({ queryKey: ["publications", clip.id], queryFn: () => api.get<Publication[]>(`/api/social/publications?clip_id=${clip.id}`), enabled: open, refetchInterval: 5000 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [copy, setCopy] = useState<SocialCopy | null>(null);
  const [captions, setCaptions] = useState<Record<string, string>>({});
  const [privacy, setPrivacy] = useState("private");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (targets.data && selected.size === 0 && !done) {
      setSelected(new Set(targets.data.targets.filter((t) => t.ready).map((t) => t.platform)));
    }
  }, [targets.data, selected.size, done]);

  const gen = useMutation({
    mutationFn: () => api.post<SocialCopy>(`/api/clips/${clip.id}/social-copy`, {}),
    onSuccess: (c) => {
      setCopy(c);
      const next: Record<string, string> = {};
      for (const p of Object.keys(COPY_KEY)) {
        const k = COPY_KEY[p];
        const desc = c.descriptions[k]?.[0] ?? "";
        const tags = (c.hashtags[k] ?? []).join(" ");
        next[p] = [desc, tags].filter(Boolean).join("\n\n");
      }
      setCaptions(next);
    },
  });

  const publish = useMutation({
    mutationFn: () => api.post(`/api/clips/${clip.id}/publish`, {
      platforms: [...selected], captions: Object.fromEntries([...selected].map((p) => [p, captions[p] ?? ""])), privacy,
    }),
    onSuccess: () => { setDone(true); qc.invalidateQueries({ queryKey: ["publications", clip.id] }); },
  });

  const hasYouTube = selected.has("shorts");
  const toggle = (p: string) => setSelected((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });

  // Presets de destino (los clips ya salen en 9:16; "Verticales" = Shorts/Reels/TikTok/Facebook Reels).
  const VERTICAL = new Set(["shorts", "reels", "tiktok", "fb_reels"]);
  const ready = targets.data?.targets.filter((t) => t.ready).map((t) => t.platform) ?? [];
  const verticals = ready.filter((p) => VERTICAL.has(p));

  return (
    <Modal open={open} onClose={onClose} title={`Publicar: ${clip.title}`} wide>
      <div className="grid gap-5 md:grid-cols-[150px_1fr]">
        <div>
          <div className="aspect-[9/16] overflow-hidden rounded-xl bg-black ring-1 ring-line">
            {clip.video_url ? <video src={withToken(clip.video_url)} muted loop className="h-full w-full object-cover" onMouseEnter={(e) => e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} /> : null}
          </div>
          <button className="btn w-full justify-center mt-3" onClick={() => gen.mutate()} disabled={gen.isPending}>
            {gen.isPending ? <Spinner /> : <Sparkles size={15} />} Generar textos
          </button>
          {copy && <div className="mt-1 text-[11px] text-ink-3">Textos por {copy.source === "llm" ? "IA" : "análisis del clip"}.</div>}
        </div>

        <div className="min-w-0 space-y-4">
          {targets.isLoading ? <Spinner /> : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="label">Redes</span>
                {ready.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Chip active={selected.size === ready.length && ready.length > 0} onClick={() => setSelected(new Set(ready))}>Todas</Chip>
                    {verticals.length > 0 && <Chip active={selected.size === verticals.length && [...selected].every((p) => VERTICAL.has(p))} onClick={() => setSelected(new Set(verticals))}>Verticales</Chip>}
                    <Chip onClick={() => setSelected(new Set())}>Ninguna</Chip>
                  </div>
                )}
              </div>
              {targets.data?.targets.map((t) => (
                <div key={t.platform} className={`rounded-xl p-3 ring-1 ${selected.has(t.platform) ? "bg-accent-soft ring-accent/50" : "bg-panel-2 ring-line"}`}>
                  <div className="flex items-center gap-2">
                    <input type="checkbox" checked={selected.has(t.platform)} disabled={!t.ready} onChange={() => toggle(t.platform)} />
                    <span className="font-semibold">{t.capabilities.label}</span>
                    {!t.ready && <span className="ml-auto flex items-center gap-1 text-[11px] text-amber-300"><TriangleAlert size={12} />{MISSING_LABEL[t.missing[0] ?? ""] ?? t.missing.join(", ")}</span>}
                    {t.ready && t.capabilities.default_privacy === "draft" && <span className="ml-auto text-[11px] text-ink-3">se sube como borrador</span>}
                  </div>
                  {selected.has(t.platform) && (
                    <textarea className="input mt-2 h-24 text-xs" placeholder={`Texto para ${t.capabilities.label}…`}
                      value={captions[t.platform] ?? ""} onChange={(e) => setCaptions({ ...captions, [t.platform]: e.target.value })} />
                  )}
                </div>
              ))}
              {hasYouTube && (
                <label className="flex items-center gap-2 text-sm text-ink-2">Privacidad de YouTube:
                  <select className="input w-auto" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                    <option value="private">Privado</option><option value="unlisted">Oculto</option><option value="public">Público</option>
                  </select>
                </label>
              )}
            </div>
          )}

          {copy && <CopyPanel copy={copy} />}

          {pubs.data?.some((p) => ["queued", "uploading", "published", "draft", "failed"].includes(p.status ?? "")) && (
            <div className="rounded-xl bg-panel-2 p-3 ring-1 ring-line">
              <div className="label mb-1.5">Estado de publicación</div>
              {pubs.data.filter((p) => (p.status ?? "registered") !== "registered").map((p) => {
                const st = p.status ?? "registered";
                return (
                <div key={p.id} className="flex items-center justify-between py-1 text-xs">
                  <span className="text-ink-2">{p.platform}</span>
                  <span className={st === "failed" ? "text-rose-300" : st === "published" || st === "draft" ? "text-emerald-300" : "text-ink-3"}>
                    {STATUS_LABEL[st] ?? st}{p.url ? <a href={p.url} target="_blank" rel="noreferrer" className="ml-1 underline">ver</a> : ""}{p.error ? ` · ${p.error}` : ""}
                  </span>
                </div>
              ); })}
            </div>
          )}

          <div className="flex items-center justify-between gap-2">
            <Link to="/settings" className="btn btn-ghost text-xs"><SettingsIcon size={14} /> Conectar redes</Link>
            {publish.error && <span className="text-xs text-rose-300">{(publish.error as Error).message}</span>}
            <button className="btn btn-primary" disabled={selected.size === 0 || publish.isPending} onClick={() => publish.mutate()}>
              {publish.isPending ? <Loader2 size={15} className="animate-spin" /> : done ? <Check size={15} /> : <Send size={15} />}
              {done ? "Publicar más" : `Publicar en ${selected.size || 0}`}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function CopyPanel({ copy }: { copy: SocialCopy }) {
  const [tab, setTab] = useState<"titles" | "x" | "recs">("titles");
  return (
    <div className="rounded-xl bg-panel-2 p-3 ring-1 ring-line">
      <div className="mb-2 flex gap-1">
        {([["titles", "Títulos"], ["x", "X (Twitter)"], ["recs", "Recomendación"]] as const).map(([id, l]) => (
          <Chip key={id} active={tab === id} onClick={() => setTab(id)}>{l}</Chip>
        ))}
      </div>
      {tab === "titles" && (
        <div className="space-y-1.5">
          {copy.titles.map((t, i) => (
            <div key={i} className={`flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm ${t.recommended ? "bg-accent-soft ring-1 ring-accent/40" : ""}`}>
              {t.recommended && <span className="mt-0.5 rounded bg-accent px-1 text-[10px] font-bold text-white">TOP</span>}
              <div><div className="text-ink">{t.text}</div>{t.reason && <div className="text-[11px] text-ink-3">{t.reason}</div>}</div>
              <CopyBtn text={t.text} />
            </div>
          ))}
        </div>
      )}
      {tab === "x" && (
        <div className="space-y-2">
          <div className="text-[11px] text-ink-3">X no se publica automáticamente; copia el texto y súbelo con tu vídeo.</div>
          {(copy.descriptions.x ?? []).map((d, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg bg-panel-3 p-2 text-sm">
              <span className="flex-1">{d} {(copy.hashtags.x ?? []).join(" ")}</span>
              <CopyBtn text={`${d} ${(copy.hashtags.x ?? []).join(" ")}`} />
            </div>
          ))}
        </div>
      )}
      {tab === "recs" && (
        <div className="space-y-1.5 text-sm text-ink-2">
          <Rec k="Mejor título" v={copy.recommendations.best_title} />
          <Rec k="Mejor plataforma" v={copy.recommendations.best_platform} />
          <Rec k="Hashtags prioritarios" v={copy.recommendations.priority_hashtags.join(" ")} />
          <Rec k="Frase de gancho" v={copy.recommendations.hook_phrase} />
          <Rec k="Alcance" v={copy.recommendations.reach_tip} />
          {copy.notes.map((n) => <div key={n} className="text-[11px] text-ink-3">· {n}</div>)}
        </div>
      )}
    </div>
  );
}

function Rec({ k, v }: { k: string; v: string }) {
  return <div><span className="text-ink-3">{k}: </span><span className="text-ink">{v}</span></div>;
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button className="btn btn-ghost ml-auto shrink-0 p-1" title="Copiar" onClick={() => { navigator.clipboard?.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200); }}>
      {ok ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
    </button>
  );
}
