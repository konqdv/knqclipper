export type Platform = "youtube" | "twitch" | "kick" | "local" | "vod" | "ingest";

export interface Capabilities {
  official_status_api: boolean;
  official_media_api: boolean;
  chat: boolean;
  chat_official: boolean;
  needs_credentials_for_status?: boolean;
  notes: string[];
}

export interface LiveState {
  is_live?: boolean;
  method?: string;
  title?: string | null;
  category?: string | null;
  viewer_count?: number | null;
  started_at?: number | null;
  error?: string | null;
  checked_at?: number;
}

export interface ChannelConfig {
  mode: "review" | "autopilot";
  min_score: number;
  buffer_seconds: number;
  pre_seconds: number;
  post_seconds: number;
  sensitivity: number;
  chat_enabled: boolean;
  realtime: boolean;
  caption_style: string | null;
  reframe_mode: string;
  language: string | null;
  min_duration: number | null;
  max_duration: number | null;
}

export interface Classification {
  primary: string;
  secondary: string[];
  scores: Record<string, number>;
  confidence: number;
  sources: string[];
}

export interface SessionSnapshot {
  session_id: number;
  channel_id: number;
  state: "starting" | "live" | "finishing" | "ended" | "error";
  error: string | null;
  segments: number;
  seconds_captured: number;
  uptime_s: number;
  buffer: { seconds: number; segments: number; pins: number; evicted: number };
  capture: { segments?: number; duplicates_skipped?: number; ads_skipped?: number; gaps?: number; reconnects?: number; bytes?: number };
  last_segment_age_s: number | null;
  asr: { enabled: boolean; lag_s: number | null; language: string | null; dropped_s: number; rtf: number | null };
  analysis_backlog: number;
  chat: { connected: boolean; messages: number; official: boolean | null; error: string | null };
  classification: Classification | null;
  signal: Record<string, number>;
  engine: Record<string, number>;
  open_candidates: number;
  transcript_tail: string;
}

export interface Channel {
  id: number;
  platform: Platform;
  url: string;
  handle: string;
  display_name: string;
  avatar_url: string | null;
  monitoring: boolean;
  rights_confirmed: boolean;
  config: ChannelConfig;
  live_state: LiveState;
  created_at: number;
  capabilities: Capabilities | null;
  session: SessionSnapshot | null;
}

export type Criterion = "hook" | "entertainment" | "narrative" | "emotion" | "flow" | "shareability" | "audio_quality";

export interface MomentSummary {
  id: number;
  channel_id: number;
  session_id: number | null;
  status: "pending" | "approved" | "rejected" | "rendered" | "discarded";
  start: number;
  end: number;
  duration: number;
  trigger_time: number;
  score: number;
  priority: "excellent" | "high" | "review" | "low";
  confidence: number;
  category: string;
  secondary_categories: string[];
  subscores: Partial<Record<Criterion, number>>;
  points: Partial<Record<Criterion, number>>;
  weights: Partial<Record<Criterion, number>>;
  reasons: string[];
  title: string;
  hook: string | null;
  transcript: string;
  created_at: number;
  thumbnail_url: string | null;
  source_url: string | null;
  source_start: number;
  source_duration: number;
  model_version: string | null;
  moment_types: string[];
  channel: { id: number; display_name: string; platform: Platform; avatar_url: string | null } | null;
  context: { what?: string; who?: string; why?: string; has_beginning?: boolean; has_ending?: boolean; standalone?: boolean; source?: string };
}

export interface Word {
  w: string;
  s: number;
  e: number;
  p?: number | null;
  spk?: string | null;
  emphasis?: boolean | null;
}

export interface MomentFull extends MomentSummary {
  words: Word[];
  signals: Record<string, unknown>;
  clip_ids: number[];
}

export interface CaptionStyle {
  style?: string;
  font?: string;
  font_size?: number;
  primary_color?: string;
  highlight_color?: string;
  outline_color?: string;
  outline?: number;
  shadow?: number;
  position?: "lower" | "middle" | "top" | "custom";
  margin_v?: number;
  pos_x?: number;
  pos_y?: number;
  uppercase?: boolean;
  max_words_per_line?: number;
  max_lines?: number;
  animation?: "pop" | "bounce" | "fade" | "none";
  highlight_scale?: number;
  emphasis?: "auto" | "none";
  box?: boolean;
  box_color?: string;
  box_opacity?: number;
  rotate?: boolean;
  enabled?: boolean;
  mode?: string;
}

export interface ReframePlan {
  layout: "track" | "split" | "fit" | "center";
  src_w: number;
  src_h: number;
  crop_w: number;
  crop_h: number;
  keyframes: [number, number][];
  y: number;
  regions: { x: number; y: number; w: number; h: number; out_h: number }[];
  reason: string;
  alternatives: string[];
}

export interface ClipEdit {
  trim_start: number;
  trim_end: number;
  words: Word[];
  caption_style: CaptionStyle;
  reframe: { mode: string; manual: { x: number; y: number; zoom: number }; plan: ReframePlan | null; plan_key: string | null };
  title: { text: string; duration: number; style: Record<string, unknown> };
  watermark: { text: string; image: string | null; position: string; opacity: number; font_size: number };
}

export interface ClipSummary {
  id: number;
  moment_id: number | null;
  channel_id: number | null;
  title: string;
  status: "draft" | "queued" | "rendering" | "ready" | "failed";
  review: "unreviewed" | "approved" | "rejected";
  duration: number;
  progress: number;
  error: string | null;
  video_url: string | null;
  thumbnail_url: string | null;
  created_at: number;
  updated_at: number;
  render_seconds: number | null;
  render_count: number;
  score: number | null;
  priority: string | null;
  category: string | null;
  caption_style: string | null;
  channel: { id: number; display_name: string; platform: Platform; avatar_url: string | null } | null;
}

export interface ClipFull extends ClipSummary {
  edit: ClipEdit;
  source_url: string | null;
  moment: MomentFull | null;
}

export interface SystemStatus {
  version: string;
  resources: {
    cpu_percent: number;
    cpu_count: number;
    ram_percent: number;
    ram_used_mb: number;
    ram_total_mb: number;
    process_rss_mb: number;
    disk_free_gb: number;
    disk_total_gb: number;
    gpus: { index: number; name: string; util_percent: number; mem_used_mb: number; mem_total_mb: number }[];
    cuda: boolean;
  };
  counts: Record<string, number>;
  sessions: Record<string, SessionSnapshot>;
  queue: Record<string, number>;
  queue_backend: string;
  asr: { model: string; loaded: boolean; device: string | null; compute_type: string | null; error: string | null; rtf: number | null };
  llm: { provider: string; model: string | null };
  disk_free_gb: number;
}

export interface Paged<T> {
  total: number;
  items: T[];
}

export interface TrainingExample {
  id: number;
  title: string;
  creator: string | null;
  platform: string | null;
  label: string;
  tags: string[];
  status: "pending" | "analyzing" | "ready" | "failed";
  duration: number;
  transcript: string;
  analysis: Record<string, any>;
  error: string | null;
  video_url: string | null;
  created_at: number;
}

export interface ModelVersion {
  id: number;
  kind: string;
  version: string;
  status: string;
  n_samples: number;
  metrics: Record<string, number>;
  notes: string | null;
  created_at: number;
  top_features: { feature: string; coef: number }[];
}

export interface Publication {
  id: number;
  clip_id: number;
  clip_title: string | null;
  platform: "tiktok" | "reels" | "shorts";
  url: string | null;
  posted_at: number | null;
  created_at: number;
  latest: Record<string, number | null> | null;
  history: number;
}

export interface InsightRow {
  group: string;
  n: number;
  avg_views: number | null;
  avg_retention: number | null;
  avg_completion: number | null;
  engagement: number | null;
  performance: number | null;
}
