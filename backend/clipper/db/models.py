"""ORM models. Timestamps are UNIX epoch seconds (float) to stay portable across SQLite/Postgres."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> float:
    return time.time()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Channel(Base):
    """A monitored creator channel / stream URL / local source."""

    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)  # youtube|twitch|kick|local|vod|ingest
    url: Mapped[str] = mapped_column(Text)
    handle: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitoring: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # mode, min_score, buffer_seconds, pre/post seconds, sensitivity, chat_enabled, caption style...
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # live state cache (last check result) — never authoritative for "processing"
    live_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class StreamSession(Base):
    """One continuous capture of a broadcast (a new session starts after the stream ends)."""

    __tablename__ = "stream_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    platform_stream_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    secondary_categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="starting")  # starting|live|ended|error
    started_at: Mapped[float] = mapped_column(Float, default=now)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    wall_start: Mapped[float | None] = mapped_column(Float, nullable=True)  # wall clock of t=0
    segments_received: Mapped[int] = mapped_column(Integer, default=0)
    seconds_captured: Mapped[float] = mapped_column(Float, default=0.0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Moment(Base):
    """A detected highlight candidate (times are stream-time seconds within the session)."""

    __tablename__ = "moments"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("stream_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected|rendered|discarded
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    trigger_time: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, index=True)
    subscores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority: Mapped[str] = mapped_column(String(16), default="review")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    category: Mapped[str] = mapped_column(String(64), default="general")
    secondary_categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    words: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{w,s,e,p,spk}] relative to source file
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # answers to the 8 context questions
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # feature vector + raw signal summary
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_start: Mapped[float] = mapped_column(Float, default=0.0)  # stream time at source t=0
    source_duration: Mapped[float] = mapped_column(Float, default=0.0)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)

    @property
    def duration(self) -> float:
        return self.end - self.start


class Clip(Base):
    """A rendered (or to-be-rendered) vertical clip derived from a moment or an upload."""

    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(primary_key=True)
    moment_id: Mapped[int | None] = mapped_column(ForeignKey("moments.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft|queued|rendering|ready|failed
    review: Mapped[str] = mapped_column(String(16), default="unreviewed")  # unreviewed|approved|rejected
    edit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    render_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    moment_id: Mapped[int | None] = mapped_column(ForeignKey("moments.id", ondelete="SET NULL"), nullable=True, index=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approve|reject|rate
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5
    mediocre: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_recut: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    modifications: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # frozen copy of what the model saw: transcript, signals, category, original score...
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class TrainingExample(Base):
    __tablename__ = "training_examples"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(16), default="normal")  # excellent|good|normal|bad|unpublishable
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|analyzing|ready|failed
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    transcript: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="editorial")  # editorial|performance
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|production|archived|rejected
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # coefficients, feature names, scaler
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Publication(Base):
    __tablename__ = "publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(16))  # tiktok|reels|shorts
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    posted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[float] = mapped_column(Float, default=now)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|csv|youtube_api
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_retention: Mapped[float | None] = mapped_column(Float, nullable=True)  # percent 0..100
    completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # percent 0..100
    watch_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    followers_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)


class AppSetting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)  # render|train_analyze|...
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)
