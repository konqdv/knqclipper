import { useEffect, useRef, useState } from 'react';
import {
  Loader,
  CheckCircle2,
  AlertCircle,
  ArrowRight,
  Clock,
  RefreshCcw,
} from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { api } from '../hooks/useApi';

const STAGE_CONFIG = {
  download: { label: 'Preparación', detail: 'Preparando el vídeo de entrada y sus metadatos.' },
  danmaku: { label: 'Procesar chat', detail: 'Obteniendo señales de interacción del público.' },
  asr: { label: 'Transcripción', detail: 'Transcribiendo el audio del vídeo.' },
  scoring: { label: 'Puntuación de momentos', detail: 'Ordenando los momentos candidatos.' },
  clipping: { label: 'Generación de clips', detail: 'Generando los clips para revisar.' },
  upload: { label: 'Resultados', detail: 'Guardando metadatos y resultados.' },
};

const STAGES = Object.keys(STAGE_CONFIG);

function normalizeLogText(text) {
  const raw = String(text || '');
  if (!raw.includes('\\n') && !raw.includes('\\t') && !raw.includes('\\r')) return raw;
  return raw.replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n').replace(/\\r/g, '\r').replace(/\\t/g, '\t');
}

function formatElapsed(s) {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m} min ${String(sec).padStart(2, '0')} s`;
}

function formatVideoTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function ProcessingView() {
  const { state, dispatch } = useAppStore();
  const pollRef = useRef(null);
  const pollErrorCountRef = useRef(0);
  const startTimeRef = useRef(null);
  const [completedResult, setCompletedResult] = useState(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    setCompletedResult(null);
    setElapsed(0);
    startTimeRef.current = null;
  }, [state.jobId]);

  useEffect(() => {
    const isRunning = state.jobStatus === 'processing' || state.jobStatus === 'queued';
    if (!isRunning) return undefined;
    if (!startTimeRef.current) startTimeRef.current = Date.now();
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [state.jobStatus]);

  useEffect(() => {
    if (!state.jobId) return undefined;

    const poll = async () => {
      try {
        const job = await api.getJob(state.jobId);
        pollErrorCountRef.current = 0;
        dispatch({ type: 'UPDATE_PROGRESS', payload: { progress: job.progress, stage: job.current_stage } });
        if (job.status === 'completed') {
          setCompletedResult({ highlights: job.clips || [], duration: job.video_duration || 0 });
          clearInterval(pollRef.current);
        } else if (job.status === 'failed') {
          dispatch({ type: 'JOB_FAILED', payload: job.error || 'Tarea fallida' });
          clearInterval(pollRef.current);
        }
      } catch {
        pollErrorCountRef.current += 1;
        if (pollErrorCountRef.current >= 8) {
          dispatch({ type: 'JOB_FAILED', payload: 'Conexión interrumpida' });
          clearInterval(pollRef.current);
        }
      }
    };

    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => clearInterval(pollRef.current);
  }, [state.jobId, dispatch]);

  useEffect(() => {
    if (!state.jobId || completedResult) return undefined;
    return api.streamProgress(state.jobId, (data) => {
      if (data.stage && data.progress !== undefined) {
        dispatch({ type: 'UPDATE_PROGRESS', payload: { progress: data.progress, stage: data.stage } });
      }
    });
  }, [state.jobId, completedResult, dispatch]);

  const currentStageIdx = STAGES.indexOf(state.currentStage || '');
  const isPipelineDone = Boolean(completedResult);
  const percent = isPipelineDone ? 100 : Math.round((state.progress || 0) * 100);
  const logs = state.logs || [];
  const latestErrorLog = [...logs].reverse().find((log) => String(log?.level || '').toLowerCase() === 'error');
  const latestErrorText = normalizeLogText(latestErrorLog?.text || '');
  const currentStage = STAGE_CONFIG[state.currentStage] || null;

  const statusTitle = state.jobStatus === 'failed'
    ? 'Procesamiento interrumpido'
    : isPipelineDone
      ? 'Clips generados, listos para revisar'
      : currentStage?.label || 'Preparando el proyecto';

  const statusText = state.jobStatus === 'failed'
    ? 'Revisa el último error de abajo; corrige el material o la autenticación y reinténtalo.'
    : isPipelineDone
      ? 'Primer procesamiento hecho; revisa y ajusta antes de exportar.'
      : currentStage?.detail || 'Esperando un proceso disponible.';

  return (
    <div className="min-h-full w-full overflow-y-auto p-4 md:p-8">
      <div className="mx-auto grid w-full max-w-[1380px] gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="surface-panel p-5 md:p-6">
          <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="mb-2 signal-label">Procesando</div>
              <h1 className="text-3xl font-extrabold tracking-tight text-text-primary md:text-4xl">
                {statusTitle}
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-7 text-text-secondary">{statusText}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <MetaChip label="Progreso" value={`${percent}%`} />
              <MetaChip label="Tiempo" value={elapsed > 0 ? formatElapsed(elapsed) : 'Iniciando'} />
            </div>
          </div>

          <div className="mb-6">
            <div className="mb-2 flex items-center justify-between text-xs text-text-muted">
              <span>Progreso</span>
              <span className="font-mono">{percent}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
              <div
                className="progress-shimmer h-full rounded-full transition-[width] duration-700"
                style={{ width: `${Math.max(4, percent)}%` }}
              />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {STAGES.map((stage, index) => {
              const config = STAGE_CONFIG[stage];
              const isActive = !isPipelineDone && state.currentStage === stage && state.jobStatus !== 'failed';
              const isDone = isPipelineDone || currentStageIdx > index || state.jobStatus === 'completed';

              return (
                <div key={stage} className={`rounded-xl border p-4 transition-all duration-300 ${
                  isActive
                    ? 'card-active-accent bg-accent/5'
                    : isDone
                      ? 'border-success/15 bg-success/3'
                      : 'border-[var(--color-border)] bg-panel-strong'
                }`}>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className={`flex h-8 w-8 items-center justify-center rounded-lg transition-all duration-300 ${
                      isDone ? 'bg-success/10 text-success' : isActive ? 'bg-accent/10 text-accent pulse-ring' : 'bg-white/5 text-text-muted'
                    }`}>
                      {isDone ? <CheckCircle2 size={15} /> : isActive ? <Loader size={15} className="animate-spin" /> : <span className="text-xs font-bold">{index + 1}</span>}
                    </div>
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                      {isDone ? 'Completado' : isActive ? 'En curso' : 'En espera'}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-text-primary">{config.label}</div>
                  <div className="mt-1 text-sm leading-6 text-text-secondary">{config.detail}</div>
                </div>
              );
            })}
          </div>

          {(completedResult || state.jobStatus === 'failed') && (
            <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-panel-strong p-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <div className="text-sm font-semibold text-text-primary">
                    {completedResult ? 'Ir a Revisión' : 'Reintentar la tarea actual'}
                  </div>
                  <div className="mt-1 text-sm text-text-secondary">
                    {completedResult ? 'Abre los clips generados y ajústalos a tu gusto.' : 'Revisa el último error antes de reiniciar.'}
                  </div>
                </div>
                {completedResult ? (
                  <button
                    onClick={() => dispatch({
                      type: 'JOB_COMPLETED',
                      payload: {
                        highlights: completedResult.highlights || [],
                        videoUrl: null,
                        duration: completedResult.duration || 0,
                      },
                    })}
                    className="btn-warm rounded-xl px-5 py-3 text-sm"
                  >
                    Continuar a Revisión
                    <ArrowRight size={15} />
                  </button>
                ) : (
                  <button
                    onClick={() => dispatch({ type: 'RESET' })}
                    className="btn-secondary rounded-xl px-5 py-3 text-sm"
                  >
                    <RefreshCcw size={15} />
                    Reiniciar
                  </button>
                )}
              </div>
            </div>
          )}
        </section>

        <div className="space-y-5">
          {completedResult && (
            <section className="surface-panel p-5">
              <div className="mb-3 text-sm font-semibold text-text-primary">Clips generados</div>
              <div className="space-y-2">
                {completedResult.highlights?.slice(0, 6).map((clip, idx) => (
                  <div key={clip.id || `clip-${idx}`} className="rounded-xl border border-[var(--color-border)] bg-panel-strong px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-text-primary">
                        Clip {String(idx + 1).padStart(2, '0')}
                      </div>
                      <div className="text-xs font-mono text-text-muted">
                        {formatVideoTime(clip.clip_start)} - {formatVideoTime(clip.clip_end)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="surface-panel p-5">
            <div className="mb-3 flex items-center gap-2 text-text-primary">
              <AlertCircle size={15} />
              <span className="text-sm font-semibold">Registro</span>
            </div>
            {latestErrorText && (
              <div className="mb-3 rounded-xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger whitespace-pre-wrap">
                {latestErrorText}
              </div>
            )}
            <div className="max-h-[420px] space-y-2 overflow-y-auto rounded-xl border border-[var(--color-border)] bg-black/25 p-4 font-mono text-xs text-text-secondary">
              {logs.length === 0 && <div className="text-text-muted">Sin registros todavía.</div>}
              {logs.map((log, idx) => {
                const level = String(log?.level || 'info').toLowerCase();
                const text = normalizeLogText(log?.text || '');
                const tone = level === 'error'
                  ? 'text-danger'
                  : level === 'warning'
                    ? 'text-warning'
                    : 'text-text-secondary';
                return (
                  <div key={`${idx}-${text}`} className={`whitespace-pre-wrap break-words ${tone}`}>
                    [{level}] {text}
                  </div>
                );
              })}
            </div>
          </section>

          {!completedResult && (
            <section className="surface-panel p-5">
              <div className="mb-2 flex items-center gap-2 text-text-primary">
                <Clock size={14} />
                <span className="text-sm font-semibold">Paso actual</span>
              </div>
              <div className="text-sm text-text-secondary">
                {currentStage?.detail || 'Tarea en cola, esperando un proceso disponible.'}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function MetaChip({ label, value }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-panel-strong px-3 py-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</span>
      <span className="ml-2 text-xs font-mono text-text-primary">{value}</span>
    </div>
  );
}
