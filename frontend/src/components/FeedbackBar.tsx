import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Star, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { Chip, Spinner } from "./ui";

const REASON_LABELS: Record<string, string> = {
  gracioso: "Gracioso", buen_gancho: "Buen gancho", historia_interesante: "Historia interesante", reaccion: "Reacción",
  polemica: "Polémica", revelacion: "Revelación", buen_contexto: "Buen contexto", util: "Útil", aburrido: "Aburrido",
  mal_cortado: "Mal cortado", demasiado_largo: "Demasiado largo", demasiado_corto: "Demasiado corto", falta_contexto: "Falta contexto",
  audio_malo: "Audio malo", duplicado: "Duplicado", sin_sentido: "Sin sentido", no_publicable: "No publicable",
};

/** Approve / reject / rate / reasons / needs-recut. Works for moments (review queue) and for clips (library). */
export function FeedbackBar({ momentId, clipId, onDone }: { momentId?: number; clipId?: number; onDone?: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const reasons = useQuery({ queryKey: ["reasons"], queryFn: () => api.get<{ approve: string[]; reject: string[] }>("/api/feedback/reasons"), staleTime: Infinity });
  const [rating, setRating] = useState<number | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [mediocre, setMediocre] = useState(false);
  const [recut, setRecut] = useState(false);
  const [mode, setMode] = useState<"approve" | "reject" | null>(null);
  const path = momentId ? `/api/moments/${momentId}/feedback` : `/api/clips/${clipId}/feedback`;

  const send = useMutation({
    mutationFn: (decision: string) => api.post<{ clip_id?: number }>(path, { decision, rating, reasons: picked, mediocre, needs_recut: recut, render: !recut }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["moments"] });
      qc.invalidateQueries({ queryKey: ["clips"] });
      qc.invalidateQueries({ queryKey: ["status"] });
      onDone?.();
    },
  });
  const editFirst = useMutation({
    mutationFn: () => api.post<{ clip_id: number }>(`/api/moments/${momentId}/clip?render=false`),
    onSuccess: (r) => nav(`/editor/${r.clip_id}`),
  });
  const toggle = (r: string) => setPicked((p) => (p.includes(r) ? p.filter((x) => x !== r) : [...p, r]));
  const options = mode === "reject" ? reasons.data?.reject : reasons.data?.approve;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button className={`btn ${mode === "approve" ? "btn-primary" : "btn-good"}`} onClick={() => setMode("approve")}><Check size={15} /> Aprobar</button>
        <button className={`btn ${mode === "reject" ? "bg-rose-600 border-rose-600 text-white" : "btn-danger"}`} onClick={() => setMode("reject")}><X size={15} /> Rechazar</button>
        {momentId && <button className="btn" onClick={() => editFirst.mutate()} disabled={editFirst.isPending}>{editFirst.isPending ? <Spinner /> : <Pencil size={15} />} Editar antes</button>}
        <div className="ml-auto flex items-center gap-0.5" aria-label="Calificación">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} onClick={() => setRating(rating === n ? null : n)} title={`${n}/5`} className="p-0.5">
              <Star size={17} className={rating && n <= rating ? "fill-amber-400 text-amber-400" : "text-ink-3"} />
            </button>
          ))}
        </div>
      </div>
      {mode && (
        <div className="space-y-2.5 rounded-xl bg-panel-2 p-3 ring-1 ring-line">
          <div className="flex flex-wrap gap-1.5">{options?.map((r) => <Chip key={r} active={picked.includes(r)} onClick={() => toggle(r)}>{REASON_LABELS[r] ?? r}</Chip>)}</div>
          <div className="flex flex-wrap items-center gap-4 text-xs text-ink-2">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={mediocre} onChange={(e) => setMediocre(e.target.checked)} /> Mediocre</label>
            {mode === "approve" && <label className="flex items-center gap-1.5"><input type="checkbox" checked={recut} onChange={(e) => setRecut(e.target.checked)} /> Necesita otro recorte (no renderizar aún)</label>}
            <button className="btn btn-primary ml-auto" disabled={send.isPending} onClick={() => send.mutate(mode)}>
              {send.isPending ? <Spinner /> : null}{mode === "approve" ? (momentId && !recut ? "Aprobar y generar clip" : "Guardar aprobación") : "Confirmar rechazo"}
            </button>
          </div>
          {send.error && <div className="text-xs text-rose-300">{(send.error as Error).message}</div>}
        </div>
      )}
      {!mode && rating && (
        <div className="flex justify-end"><button className="btn" onClick={() => send.mutate("rate")}>Guardar calificación</button></div>
      )}
    </div>
  );
}
