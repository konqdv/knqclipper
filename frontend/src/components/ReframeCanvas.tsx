import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import type { ClipEdit, ReframePlan } from "../lib/types";

function xAt(kf: [number, number][], t: number): number {
  if (!kf.length) return 0;
  if (t <= kf[0][0]) return kf[0][1];
  for (let i = 1; i < kf.length; i++) {
    const [t0, x0] = kf[i - 1];
    const [t1, x1] = kf[i];
    if (t < t1) return t1 - t0 < 1e-3 ? x1 : x0 + ((x1 - x0) * (t - t0)) / (t1 - t0);
  }
  return kf[kf.length - 1][1];
}

/** Draws the 9:16 output frame from the source video exactly as the planner will crop it (track/split/fit/center/manual). */
export const ReframeCanvas = forwardRef<HTMLVideoElement, {
  src: string; edit: ClipEdit; width: number; onTime: (t: number) => void; onMeta?: (w: number, h: number, d: number) => void;
}>(function ReframeCanvas({ src, edit, width, onTime, onMeta }, ref) {
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  useImperativeHandle(ref, () => video.current!);
  const height = Math.round((width * 16) / 9);
  const editRef = useRef(edit);
  editRef.current = edit;

  const draw = useCallback(() => {
    const v = video.current;
    const c = canvas.current;
    if (!v || !c || !v.videoWidth) return;
    const ctx = c.getContext("2d")!;
    const W = v.videoWidth;
    const H = v.videoHeight;
    const e = editRef.current;
    const plan: ReframePlan | null = e.reframe.plan;
    const mode = e.reframe.mode;
    const tClip = v.currentTime - e.trim_start;
    ctx.filter = "none";
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, c.width, c.height);
    const layout = mode === "manual" ? "manual" : mode === "fit" ? "fit" : mode === "center" ? "center" : plan?.layout ?? "center";
    if (layout === "fit") {
      const s = Math.max(c.width / W, c.height / H);
      ctx.filter = "blur(14px) brightness(0.85)";
      ctx.drawImage(v, (c.width - W * s) / 2, (c.height - H * s) / 2, W * s, H * s);
      ctx.filter = "none";
      const h = (c.width * H) / W;
      ctx.drawImage(v, 0, (c.height - h) / 2, c.width, h);
    } else if (layout === "split" && plan?.regions?.length) {
      let y = 0;
      for (const r of plan.regions) {
        const dh = (r.out_h / 1920) * c.height;
        let sw = r.w * W;
        let sh = r.h * H;
        let sx = r.x * W;
        let sy = r.y * H;
        const target = c.width / dh;
        if (sw / sh > target) { const nw = sh * target; sx += (sw - nw) / 2; sw = nw; } else { const nh = sw / target; sy += (sh - nh) / 2; sh = nh; }
        ctx.drawImage(v, sx, sy, sw, sh, 0, y, c.width, dh);
        y += dh;
      }
    } else if (layout === "manual") {
      const m = e.reframe.manual;
      const zoom = Math.max(1, m.zoom ?? 1);
      const ch = H / zoom;
      const cw = Math.min(W, (H * 9) / 16 / zoom);
      const sx = Math.max(0, Math.min(W - cw, m.x * W - cw / 2));
      const sy = Math.max(0, Math.min(H - ch, m.y * H - ch / 2));
      ctx.drawImage(v, sx, sy, cw, ch, 0, 0, c.width, c.height);
    } else {
      const cw = plan?.crop_w || Math.min(W, (H * 9) / 16);
      const ch = plan?.crop_h || H;
      const sx = layout === "center" || !plan?.keyframes?.length ? (W - cw) / 2 : (xAt(plan.keyframes, tClip) * W) / (plan.src_w || W);
      ctx.drawImage(v, Math.max(0, Math.min(W - cw, sx)), plan?.y ?? 0, cw, ch, 0, 0, c.width, c.height);
    }
  }, []);

  useEffect(() => {
    let raf = 0;
    let last = -1;
    const loop = () => {
      draw();
      const t = video.current?.currentTime ?? 0;
      if (Math.abs(t - last) > 0.03) {
        last = t;
        onTime(t);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [draw, onTime]);

  return (
    <div className="relative" style={{ width, height }}>
      <video ref={video} src={src} preload="auto" playsInline crossOrigin="anonymous" className="hidden"
        onLoadedMetadata={(e) => { const v = e.currentTarget; onMeta?.(v.videoWidth, v.videoHeight, v.duration); v.currentTime = editRef.current.trim_start; }}
        onTimeUpdate={(e) => { const v = e.currentTarget; if (!v.paused && v.currentTime >= editRef.current.trim_end) { v.pause(); v.currentTime = editRef.current.trim_start; } }} />
      <canvas ref={canvas} width={width * 2} height={height * 2} style={{ width, height }} className="rounded-xl bg-black" />
    </div>
  );
});
