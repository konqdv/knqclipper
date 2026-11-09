import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { getToken } from "./api";
import type { SessionSnapshot } from "./types";

export interface LiveEvent {
  type: string;
  data: Record<string, any>;
  ts: number;
}

interface EventsCtx {
  connected: boolean;
  sessions: Record<number, SessionSnapshot>;
  transcripts: Record<number, { start: number; text: string; speaker: string | null }[]>;
  clipProgress: Record<number, number>;
  recent: LiveEvent[];
  previewUrls: Record<number, string>;
}

const Ctx = createContext<EventsCtx>({ connected: false, sessions: {}, transcripts: {}, clipProgress: {}, recent: [], previewUrls: {} });

export function useEvents() {
  return useContext(Ctx);
}

/** One WebSocket for the whole app: keeps live session stats and invalidates cached queries on changes. */
export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<EventsCtx>({ connected: false, sessions: {}, transcripts: {}, clipProgress: {}, recent: [], previewUrls: {} });
  const retry = useRef(1000);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const t = getToken();
      ws = new WebSocket(`${proto}://${location.host}/api/ws${t ? `?token=${encodeURIComponent(t)}` : ""}`);
      ws.onopen = () => {
        retry.current = 1000;
        setState((s) => ({ ...s, connected: true }));
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed) timer = setTimeout(connect, (retry.current = Math.min(retry.current * 2, 15000)));
      };
      ws.onmessage = (m) => {
        const ev: LiveEvent = JSON.parse(m.data);
        handle(ev);
      };
    };

    const handle = (ev: LiveEvent) => {
      const d = ev.data;
      switch (ev.type) {
        case "stream.stats":
          setState((s) => ({ ...s, sessions: { ...s.sessions, [d.channel_id]: d as SessionSnapshot } }));
          return;
        case "stream.transcript":
          setState((s) => {
            const prev = s.transcripts[d.channel_id] ?? [];
            const next = [...prev, ...d.segments].slice(-40);
            return { ...s, transcripts: { ...s.transcripts, [d.channel_id]: next } };
          });
          return;
        case "clip.progress":
          if (!d.preview) setState((s) => ({ ...s, clipProgress: { ...s.clipProgress, [d.clip_id]: d.progress } }));
          return;
        case "clip.preview_ready":
          setState((s) => ({ ...s, previewUrls: { ...s.previewUrls, [d.clip_id]: d.url } }));
          return;
        case "stream.state":
          if (d.state === "ended" || d.state === "error")
            setState((s) => {
              const { [d.channel_id]: _, ...rest } = s.sessions;
              return { ...s, sessions: rest };
            });
          qc.invalidateQueries({ queryKey: ["channels"] });
          break;
        case "channel.status":
        case "channel.monitoring":
          qc.invalidateQueries({ queryKey: ["channels"] });
          break;
        case "moment.detected":
          qc.invalidateQueries({ queryKey: ["moments"] });
          break;
        case "clip.created":
        case "clip.rendering":
        case "clip.ready":
        case "clip.failed":
          qc.invalidateQueries({ queryKey: ["clips"] });
          if (ev.type !== "clip.created") qc.invalidateQueries({ queryKey: ["clip", d.clip_id] });
          break;
        case "training.ready":
        case "training.analyzing":
          qc.invalidateQueries({ queryKey: ["training"] });
          break;
        case "learning.trained":
          qc.invalidateQueries({ queryKey: ["models"] });
          break;
        case "ping":
        case "hello":
          return;
      }
      setState((s) => ({ ...s, recent: [ev, ...s.recent].slice(0, 50) }));
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}
