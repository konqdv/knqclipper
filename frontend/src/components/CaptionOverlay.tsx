import type { CSSProperties } from "react";
import type { CaptionStyle, Word } from "../lib/types";

/** Client-side mirror of backend presets (clipper/captions/styles.py) so the live preview matches the render. */
export const PRESETS: Record<string, CaptionStyle> = {
  word: { mode: "word", font: "Montserrat Black", font_size: 96, uppercase: true, animation: "pop", outline: 7, shadow: 3, highlight_color: "#FFE600", emphasis: "auto", position: "middle" },
  phrase: { mode: "phrase", font: "Montserrat ExtraBold", font_size: 76, uppercase: false, animation: "fade", outline: 6, shadow: 2, max_words_per_line: 5, max_lines: 2, emphasis: "auto" },
  karaoke: { mode: "karaoke", font: "Montserrat Black", font_size: 80, uppercase: true, animation: "none", outline: 6, shadow: 2, max_words_per_line: 4, highlight_color: "#FFE600" },
  dynamic: { mode: "dynamic", font: "Montserrat Black", font_size: 84, uppercase: true, animation: "pop", outline: 7, shadow: 3, max_words_per_line: 3, highlight_color: "#FFE600", highlight_scale: 112 },
  podcast: { mode: "dynamic", font: "Poppins ExtraBold", font_size: 62, uppercase: false, animation: "fade", outline: 0, shadow: 0, box: true, box_color: "#000000", box_opacity: 0.55, max_words_per_line: 6, max_lines: 2, highlight_color: "#7CF29A", highlight_scale: 100, position: "lower", margin_v: 330 },
  gaming: { mode: "dynamic", font: "Bangers", font_size: 104, uppercase: true, animation: "bounce", outline: 9, shadow: 4, max_words_per_line: 2, highlight_color: "#39FF14", highlight_scale: 120, rotate: true, position: "middle" },
  minimal: { mode: "phrase", font: "Inter SemiBold", font_size: 56, uppercase: false, animation: "fade", outline: 2, shadow: 0, max_words_per_line: 7, max_lines: 2, emphasis: "none", margin_v: 260 },
};

const BASE: CaptionStyle = {
  style: "dynamic", mode: "dynamic", font: "Montserrat Black", font_size: 84, primary_color: "#FFFFFF", highlight_color: "#FFE600",
  outline_color: "#000000", outline: 6, shadow: 3, position: "lower", margin_v: 420, pos_x: 0.5, pos_y: 0.72, uppercase: true,
  max_words_per_line: 4, max_lines: 1, animation: "pop", highlight_scale: 110, emphasis: "auto", box: false, box_color: "#000000",
  box_opacity: 0.5, rotate: false, enabled: true,
};

export function resolveStyle(s: CaptionStyle | undefined): CaptionStyle {
  const style = s?.style ?? "dynamic";
  const out: CaptionStyle = { ...BASE, ...(PRESETS[style] ?? {}) };
  for (const [k, v] of Object.entries(s ?? {})) if (v !== null && v !== undefined) (out as any)[k] = v;
  out.style = style;
  return out;
}

const EMPH = new Set(["nunca", "siempre", "jamas", "never", "always", "best", "worst", "wow", "omg", "what", "dios", "literally", "literalmente", "nadie", "nobody", "everyone", "increible", "insane", "serious"]);
const norm = (w: string) => w.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^\p{L}\p{N}]/gu, "");
function isEmph(w: Word, st: CaptionStyle) {
  if (w.emphasis != null) return !!w.emphasis;
  if (st.emphasis !== "auto") return false;
  const n = norm(w.w);
  return EMPH.has(n) || /\d/.test(n) || (w.w === w.w.toUpperCase() && w.w.length > 2 && /\p{L}/u.test(w.w));
}

export function groupPages(words: Word[], maxWords: number, maxLines: number, maxChars = 22): Word[][] {
  const pages: Word[][] = [];
  let cur: Word[] = [];
  const limit = maxWords * Math.max(1, maxLines);
  words.forEach((w, i) => {
    cur.push(w);
    const nxt = words[i + 1];
    const chars = cur.reduce((a, x) => a + x.w.length + 1, 0);
    if (cur.length >= limit || chars >= maxChars * Math.max(1, maxLines) || /[.!?…]$/.test(w.w) || (nxt && nxt.s - w.e > 0.6) || !nxt) {
      pages.push(cur);
      cur = [];
    }
  });
  return pages;
}

/** Renders the caption that is visible at source time `t`. Coordinates are in the 1080x1920 output space scaled by `scale`. */
export function CaptionOverlay({ words, t, style, scale, title, titleDuration = 3, clipStart, watermark }: {
  words: Word[]; t: number; style: CaptionStyle; scale: number; title?: string; titleDuration?: number; clipStart: number;
  watermark?: { text: string; position: string; opacity: number; font_size: number };
}) {
  const st = resolveStyle(style);
  const mode = st.mode ?? "dynamic";
  const disp = (w: string) => (st.uppercase ? w.toUpperCase() : w);
  const fs = (st.font_size ?? 84) * scale;
  const outline = (st.outline ?? 6) * scale;
  const textStyle: CSSProperties = {
    fontFamily: `"${st.font}", sans-serif`, fontSize: fs, lineHeight: 1.12, color: st.primary_color,
    WebkitTextStroke: !st.box && outline ? `${outline * 2}px ${st.outline_color}` : undefined, paintOrder: "stroke fill",
    textShadow: st.shadow ? `${st.shadow * scale}px ${st.shadow * scale}px 0 rgba(0,0,0,0.65)` : undefined,
  };
  let pos: CSSProperties = { left: 0, right: 0, bottom: (st.margin_v ?? 420) * scale };
  if (st.position === "middle") pos = { left: 0, right: 0, top: "50%", transform: "translateY(-50%)" };
  if (st.position === "top") pos = { left: 0, right: 0, top: (st.margin_v ?? 260) * scale };
  if (st.position === "custom") pos = { left: `${(st.pos_x ?? 0.5) * 100}%`, top: `${(st.pos_y ?? 0.72) * 100}%`, transform: "translate(-50%,-50%)" };

  let body: React.ReactNode = null;
  let key = "none";
  if (st.enabled !== false) {
    if (mode === "word") {
      const w = words.find((x) => x.s <= t && t < Math.max(x.e, x.s + 0.08));
      if (w) {
        key = `w${w.s}`;
        body = <span style={{ color: isEmph(w, st) ? st.highlight_color : undefined }}>{disp(w.w)}</span>;
      }
    } else {
      const pages = groupPages(words, st.max_words_per_line ?? 4, st.max_lines ?? 1);
      const page = pages.find((p) => p[0].s <= t && t < p[p.length - 1].e + 0.05);
      if (page) {
        key = `p${page[0].s}`;
        const half = (st.max_lines ?? 1) > 1 && page.length > (st.max_words_per_line ?? 4) ? Math.ceil(page.length / 2) : page.length;
        body = page.map((w, i) => {
          const current = w.s <= t && t < (page[i + 1]?.s ?? w.e + 0.05);
          let color = isEmph(w, st) && mode !== "karaoke" ? st.highlight_color : undefined;
          let transform: string | undefined;
          if (mode === "dynamic" && current) {
            color = st.highlight_color;
            transform = `scale(${(st.highlight_scale ?? 110) / 100})`;
          }
          if (mode === "karaoke") {
            const p = Math.max(0, Math.min(1, (t - w.s) / Math.max(0.05, w.e - w.s)));
            return (
              <span key={i}>
                {i === half && <br />}
                <span style={{ backgroundImage: `linear-gradient(90deg, ${st.highlight_color} ${p * 100}%, ${st.primary_color} ${p * 100}%)`, WebkitBackgroundClip: "text", color: "transparent", display: "inline-block" }}>{disp(w.w)}</span>{" "}
              </span>
            );
          }
          return (
            <span key={i}>
              {i === half && <br />}
              <span style={{ color, display: "inline-block", transform, transition: "transform 80ms" }}>{disp(w.w)}</span>{" "}
            </span>
          );
        });
      }
    }
  }
  const anim = st.animation === "pop" ? "cap-pop 160ms ease-out" : st.animation === "bounce" ? "cap-bounce 250ms ease-out" : st.animation === "fade" ? "cap-fade 120ms ease-out" : undefined;
  const clipT = t - clipStart;
  const wmPos: Record<string, CSSProperties> = {
    "top-left": { top: 60 * scale, left: 50 * scale }, "top-right": { top: 60 * scale, right: 50 * scale },
    "bottom-left": { bottom: 60 * scale, left: 50 * scale }, "bottom-right": { bottom: 60 * scale, right: 50 * scale },
  };

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {title && clipT >= 0 && clipT < titleDuration && (
        <div className="absolute inset-x-0 text-center" style={{ top: 200 * scale }}>
          <span style={{ fontFamily: '"Montserrat Black"', fontSize: 72 * scale, background: "rgba(0,0,0,0.85)", padding: `${8 * scale}px ${18 * scale}px`, color: "white", animation: "cap-fade 150ms" }}>{title}</span>
        </div>
      )}
      {body && (
        <div className="absolute text-center" style={{ ...pos, padding: `0 ${60 * scale}px` }}>
          <div key={key} style={{ ...textStyle, animation: anim, display: "inline-block", transform: st.rotate ? `rotate(${key.length % 2 ? 2 : -2}deg)` : undefined,
            background: st.box ? `${st.box_color}${Math.round((st.box_opacity ?? 0.5) * 255).toString(16).padStart(2, "0")}` : undefined,
            padding: st.box ? `${6 * scale}px ${14 * scale}px` : undefined, borderRadius: st.box ? 6 * scale : undefined }}>
            {body}
          </div>
        </div>
      )}
      {watermark?.text && (
        <div className="absolute" style={{ ...wmPos[watermark.position] ?? wmPos["top-right"], fontFamily: '"Inter SemiBold"', fontSize: (watermark.font_size ?? 40) * scale, color: `rgba(255,255,255,${watermark.opacity})`, WebkitTextStroke: `${2 * scale}px rgba(0,0,0,0.4)`, paintOrder: "stroke fill" }}>
          {watermark.text}
        </div>
      )}
    </div>
  );
}
