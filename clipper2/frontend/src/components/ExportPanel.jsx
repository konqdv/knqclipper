import { useState } from 'react';
import {
  ArrowLeft,
  Download,
  Smartphone,
  Monitor,
  Square,
  CheckCircle2,
  Loader,
  Trash2,
} from 'lucide-react';
import { useAppStore } from '../store/useAppStore';
import { api } from '../hooks/useApi';
import Toggle from './Toggle';

const PLATFORMS = [
  { id: 'youtube_shorts', name: 'YouTube Shorts', aspect: '9:16', maxDuration: 60, desc: 'Vertical, máx. 60 s' },
  { id: 'tiktok', name: 'TikTok', aspect: '9:16', maxDuration: 180, desc: 'Vertical (hasta 3 min)' },
  { id: 'instagram_reels', name: 'Instagram Reels', aspect: '9:16', maxDuration: 90, desc: 'Vertical, máx. 90 s' },
  { id: 'facebook_reels', name: 'Facebook Reels', aspect: '9:16', maxDuration: 90, desc: 'Vertical, máx. 90 s' },
  { id: 'douyin', name: 'Douyin', aspect: '9:16', maxDuration: 60, desc: 'Short vertical' },
  { id: 'bilibili', name: 'Bilibili', aspect: '16:9', maxDuration: 600, desc: 'Vídeo horizontal estándar' },
  { id: 'custom', name: 'Personalizado', aspect: '16:9', maxDuration: null, desc: 'Mantener proporción original' },
];

const ASPECT_OPTIONS = [
  { id: '16:9', label: '16:9', icon: Monitor },
  { id: '9:16', label: '9:16', icon: Smartphone },
  { id: '1:1', label: '1:1', icon: Square },
];

export default function ExportPanel() {
  const { state, dispatch } = useAppStore();
  const [selectedPlatform, setSelectedPlatform] = useState(state.exportPlatform || 'bilibili');
  const [exporting, setExporting] = useState(false);
  const [exported, setExported] = useState(false);
  const [aspect, setAspect] = useState('16:9');
  const [exportError, setExportError] = useState('');
  const [cleanupInfo, setCleanupInfo] = useState('');
  const [autoCleanupSource, setAutoCleanupSource] = useState(() => localStorage.getItem('auto_cleanup_source') !== '0');
  const [keepOnlySelected, setKeepOnlySelected] = useState(() => localStorage.getItem('keep_only_selected_clips') !== '0');

  const selectedClips = state.highlights.filter((h) => h.selected);
  const platform = PLATFORMS.find((p) => p.id === selectedPlatform);
  const totalDuration = Math.round(selectedClips.reduce((sum, c) => sum + (c.clipEnd - c.clipStart), 0));

  const downloadSingleClip = async (clip, idx) => {
    if (!clip.downloadUrl) return;
    const resp = await fetch(clip.downloadUrl, { credentials: 'include' });
    if (!resp.ok) {
      throw new Error(`Error al descargar (HTTP ${resp.status})`);
    }
    const blob = await resp.blob();
    const objectUrl = URL.createObjectURL(blob);
    try {
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = clip.fileName || `clip_${String(idx + 1).padStart(2, '0')}.mp4`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  };

  const triggerDownloads = async () => {
    const failed = [];
    for (let i = 0; i < selectedClips.length; i += 1) {
      const clip = selectedClips[i];
      try {
        // eslint-disable-next-line no-await-in-loop
        await downloadSingleClip(clip, i);
      } catch (err) {
        failed.push({ clip, err });
      }
    }
    return failed;
  };

  const handleExport = async () => {
    setExportError('');
    setCleanupInfo('');
    const downloadable = selectedClips.filter((c) => !!c.downloadUrl);
    if (downloadable.length === 0) {
      setExportError('La tarea actual no tiene clips descargables.');
      return;
    }
    setExporting(true);
    const failed = await triggerDownloads();
    if (failed.length > 0) {
      setExportError(`${failed.length} clips no se pudieron descargar, reinténtalo.`);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
    const cleanupMessages = [];

    if (autoCleanupSource && state.jobId) {
      try {
        const res = await api.cleanupJobSource(state.jobId);
        const freed = Number.isFinite(res?.freed_mb) ? res.freed_mb : 0;
        cleanupMessages.push(`Material de origen limpiado (${freed} MB liberados).`);
      } catch (err) {
        cleanupMessages.push(`Limpieza del material de origen omitida: ${err?.message || 'La solicitud de limpieza falló'}`);
      }
    }

    if (keepOnlySelected && state.jobId && selectedClips.length > 0) {
      try {
        const res = await api.cleanupUnselectedClips(
          state.jobId,
          selectedClips.map((c) => c.id),
        );
        const removedCount = Number.isFinite(res?.removed_count) ? res.removed_count : 0;
        const freed = Number.isFinite(res?.freed_mb) ? res.freed_mb : 0;
        cleanupMessages.push(`Clips no seleccionados limpiados (${removedCount} borrados, ${freed} MB liberados).`);
        dispatch({ type: 'KEEP_ONLY_SELECTED_CLIPS' });
      } catch (err) {
        cleanupMessages.push(`Limpieza de clips no seleccionados omitida: ${err?.message || 'La solicitud de limpieza falló'}`);
      }
    }

    if (cleanupMessages.length > 0) {
      setCleanupInfo(cleanupMessages.join(' '));
    }
    setExporting(false);
    setExported(true);
  };

  return (
    <div className="min-h-full w-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex w-full max-w-[1380px] flex-col gap-4">
        <section className="surface-panel overflow-hidden px-5 py-5 md:px-7 md:py-6">
          <div>
            <div className="mb-2 signal-label">Exportar</div>
            <h1 className="max-w-2xl text-3xl font-extrabold tracking-tight text-text-primary md:text-4xl">
              Empaqueta los clips que conservaste y expórtalos de un clic.
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-text-secondary">
              Confirma el formato, descarga los clips elegidos y limpia el origen tras exportar.
            </p>
          </div>
        </section>

        <div className="grid gap-4">
          <section className="surface-panel p-5 md:p-6">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                <StatChip label="Clips seleccionados" value={`${selectedClips.length}`} />
                <StatChip label="Duración total" value={`${totalDuration}s`} />
                <StatChip label="Formato" value={`${aspect} MP4`} />
              </div>
              <button
                onClick={() => dispatch({ type: 'SET_PHASE', payload: 'review' })}
                className="btn-secondary rounded-lg px-4 py-2 text-xs font-semibold"
              >
                <ArrowLeft size={14} />
                Volver a Revisión
              </button>
            </div>

            <div className="grid gap-5 xl:grid-cols-2 xl:items-start">
              <div className="space-y-4">
                <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong p-5">
                  <div className="section-eyebrow mb-4">Plataforma</div>
                  <div className="grid gap-2.5 sm:grid-cols-2">
                    {PLATFORMS.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => {
                          setSelectedPlatform(p.id);
                          setAspect(p.aspect);
                        }}
                        className={`rounded-xl border px-4 py-3.5 text-left transition-all duration-200 ${
                          selectedPlatform === p.id
                            ? 'card-active-accent bg-accent/4'
                            : 'border-[var(--color-border)] bg-black/15 hover:bg-bg-hover hover:border-[var(--color-border-active)]'
                        }`}
                      >
                        <div className="text-sm font-semibold text-text-primary">{p.name}</div>
                        <div className="mt-1 text-sm text-text-secondary">{p.desc}</div>
                        <div className="mt-2 text-xs font-mono text-text-muted">
                          {p.maxDuration ? `máx. ${p.maxDuration}s` : 'Sin límite fijo de duración'}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong p-5">
                  <div className="section-eyebrow mb-4">Proporción</div>
                  <div className="grid gap-2.5 sm:grid-cols-3">
                    {ASPECT_OPTIONS.map((opt) => (
                      <button
                        key={opt.id}
                        onClick={() => setAspect(opt.id)}
                        className={`inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-3 text-sm font-semibold transition-colors ${
                          aspect === opt.id
                            ? 'border-accent/25 bg-accent/5 text-text-primary'
                            : 'border-[var(--color-border)] bg-black/15 text-text-secondary hover:bg-bg-hover'
                        }`}
                      >
                        <opt.icon size={15} />
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <aside className="flex flex-col gap-4">
                <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong p-5">
                  <div className="mb-3 text-sm font-semibold text-text-primary">Clips seleccionados</div>
                  {selectedClips.length === 0 ? (
                    <div className="text-sm leading-6 text-text-muted">No has seleccionado clips. Vuelve a Revisión y marca lo que quieras exportar.</div>
                  ) : (
                    <div className="space-y-1.5">
                      {selectedClips.map((clip, idx) => {
                        const scorePercent = Math.round((clip.score || 0) * 100);
                        return (
                          <div key={clip.id} className="rounded-lg border border-[var(--color-border)] bg-black/20 px-3.5 py-2.5">
                            <div className="flex items-center justify-between gap-3">
                              <div className="text-sm font-semibold text-text-primary">
                                Clip {String(idx + 1).padStart(2, '0')}
                              </div>
                              <div className="text-xs font-mono text-text-muted">{scorePercent}</div>
                            </div>
                            <div className="mt-1 text-xs font-mono text-text-secondary">
                              {formatTime(clip.clipStart)} - {formatTime(clip.clipEnd)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong p-5">
                  <div className="text-sm font-semibold text-text-primary">Comprobaciones</div>
                  <div className="mt-3 space-y-1.5">
                    <ChecklistItem done={selectedClips.length > 0} text="Al menos un clip seleccionado" />
                    <ChecklistItem done={Boolean(platform)} text="Plataformas seleccionadas" />
                    <ChecklistItem done={Boolean(aspect)} text="Proporción establecida" />
                  </div>
                </div>

                <div className="rounded-xl border border-[var(--color-border)] bg-panel-strong p-5">
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg bg-warm/8 text-warm">
                          <Trash2 size={15} />
                        </div>
                        <div>
                          <div className="text-sm font-semibold text-text-primary">Limpiar el material de origen automáticamente</div>
                          <div className="mt-1 max-w-xl text-sm leading-6 text-text-secondary">
                            Al exportar, borra el material de origen en caché; los clips generados se conservan.
                          </div>
                        </div>
                      </div>
                      <Toggle
                        checked={autoCleanupSource}
                        onChange={(checked) => {
                          setAutoCleanupSource(checked);
                          localStorage.setItem('auto_cleanup_source', checked ? '1' : '0');
                        }}
                      />
                    </div>

                    <div className="h-px w-full bg-[var(--color-border)]" />

                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg bg-warm/8 text-warm">
                          <Trash2 size={15} />
                        </div>
                        <div>
                          <div className="text-sm font-semibold text-text-primary">Conservar solo los clips exportados</div>
                          <div className="mt-1 max-w-xl text-sm leading-6 text-text-secondary">
                            Tras exportar, borra los clips no marcados para no acumular candidatos.
                          </div>
                        </div>
                      </div>
                      <Toggle
                        checked={keepOnlySelected}
                        onChange={(checked) => {
                          setKeepOnlySelected(checked);
                          localStorage.setItem('keep_only_selected_clips', checked ? '1' : '0');
                        }}
                      />
                    </div>
                  </div>
                </div>

                {exportError && (
                  <div className="rounded-xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger">
                    {exportError}
                  </div>
                )}

                {exported && (
                  <div className="rounded-xl border border-success/20 bg-success/5 px-5 py-4">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success/10 text-success">
                        <CheckCircle2 size={18} />
                      </div>
                      <div>
                        <div className="text-sm font-semibold text-text-primary">Exportación completada</div>
                        <div className="text-sm text-text-secondary">Tus clips seleccionados ya se pueden descargar.</div>
                      </div>
                    </div>
                    {cleanupInfo && <div className="mt-3 text-sm text-text-muted">{cleanupInfo}</div>}
                  </div>
                )}

                <button
                  onClick={handleExport}
                  disabled={exporting || selectedClips.length === 0}
                  className="btn-warm w-full rounded-xl px-6 py-3.5 text-sm xl:mt-auto"
                >
                  {exporting ? (
                    <>
                      <Loader size={15} className="animate-spin" />
                      Exportando…
                    </>
                  ) : (
                    <>
                      <Download size={15} />
                      Exportar {selectedClips.length} clips
                    </>
                  )}
                </button>
              </aside>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function StatChip({ label, value }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-panel-strong px-3 py-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</span>
      <span className="ml-2 text-xs font-mono text-text-primary">{value}</span>
    </div>
  );
}

function ChecklistItem({ done, text }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-black/15 px-3 py-2 text-sm text-text-secondary">
      <span className={`h-2 w-2 rounded-full ${done ? 'bg-success' : 'bg-text-muted'}`} />
      {text}
    </div>
  );
}

function formatTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}
