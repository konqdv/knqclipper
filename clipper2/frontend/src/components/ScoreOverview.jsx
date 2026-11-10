import { useState } from 'react';
import { Download, BrainCircuit } from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { api } from '../hooks/useApi';

const SCORE_BANDS = [
  { label: '75-100', min: 0.75, max: 1.01, color: 'var(--color-warm)' },
  { label: '50-74', min: 0.5, max: 0.75, color: 'var(--color-accent)' },
  { label: '25-49', min: 0.25, max: 0.5, color: 'rgba(124, 109, 238, 0.4)' },
  { label: '0-24', min: 0, max: 0.25, color: 'var(--color-text-muted)' },
];

export default function ScoreOverview({ totalClips, selectedCount, totalDuration, avgScore }) {
  const { state, dispatch } = useAppStore();
  const [retraining, setRetraining] = useState(false);

  const handleRetrain = async () => {
    setRetraining(true);
    try {
      await api.retrainFeedbackModel({});
    } catch {
      // Keep current model if retraining fails.
    } finally {
      setRetraining(false);
    }
  };

  const highlights = state.highlights;
  const maxBandCount = Math.max(
    1,
    ...SCORE_BANDS.map((band) => highlights.filter((h) => h.score >= band.min && h.score < band.max).length)
  );

  return (
    <div className="flex h-full flex-col justify-between">
      <div>
        <div className="mb-5">
          <div className="signal-label">Análisis</div>
          <h3 className="mt-1 text-base font-semibold text-text-primary">Resumen de puntuación</h3>
        </div>

        <div className="mb-6 grid grid-cols-2 gap-2.5">
          <StatCard label="Clips conservados" value={`${selectedCount}/${totalClips}`} />
          <StatCard label="Puntuación media" value={`${avgScore}`} />
          <StatCard label="Duración total" value={`${totalDuration}s`} />
          <StatCard label="Duración media" value={`${selectedCount > 0 ? Math.max(1, Math.round(totalDuration / selectedCount)) : 0}s`} />
        </div>

        {highlights.length > 0 && (
          <div>
            <div className="mb-3 signal-label">Distribución</div>
            <div className="space-y-2.5">
              {SCORE_BANDS.map((band) => {
                const count = highlights.filter((h) => h.score >= band.min && h.score < band.max).length;
                const barW = (count / maxBandCount) * 100;
                return (
                  <div key={band.label} className="flex items-center gap-3">
                    <span className="w-12 shrink-0 text-right text-[10px] font-mono text-text-muted">{band.label}</span>
                    <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-white/5">
                      <div className="h-full rounded-full transition-all" style={{ width: `${barW}%`, background: band.color }} />
                    </div>
                    <span className="w-4 shrink-0 text-[11px] font-mono text-text-secondary">{count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <div className="mt-6 flex gap-2.5">
        <button
          onClick={() => dispatch({ type: 'SET_EXPORT_PLATFORM', payload: 'bilibili' })}
          className="btn-warm flex-1 rounded-xl px-5 py-3 text-sm"
        >
          <Download size={15} />
          Exportar
        </button>
        <button
          onClick={handleRetrain}
          disabled={retraining}
          title="Reentrenar el modelo de feedback"
          className="btn-secondary rounded-xl px-4"
        >
          <BrainCircuit size={15} className={retraining ? 'animate-pulse' : ''} />
        </button>
      </div>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong px-3.5 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</div>
      <div className="mt-1.5 text-xl font-extrabold tracking-tight text-text-primary">{value}</div>
    </div>
  );
}
