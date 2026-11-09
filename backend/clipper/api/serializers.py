"""DB rows → JSON payloads for the dashboard."""

from __future__ import annotations

from typing import Any

from clipper.connectors.registry import BY_PLATFORM
from clipper.core.appsettings import channel_config
from clipper.db.models import Channel, Clip, Moment
from clipper.storage.manager import get_storage


def channel_out(ch: Channel, session: dict[str, Any] | None = None) -> dict[str, Any]:
    cls = BY_PLATFORM.get(ch.platform)
    return {
        "id": ch.id, "platform": ch.platform, "url": ch.url, "handle": ch.handle, "display_name": ch.display_name,
        "avatar_url": ch.avatar_url, "monitoring": ch.monitoring, "rights_confirmed": ch.rights_confirmed,
        "config": channel_config(ch.config), "live_state": ch.live_state or {}, "created_at": ch.created_at,
        "capabilities": cls.capabilities.to_dict() if cls else None, "session": session,
    }


def moment_out(m: Moment, channel: Channel | None = None, full: bool = False) -> dict[str, Any]:
    st = get_storage()
    subs = m.subscores or {}
    out = {
        "id": m.id, "channel_id": m.channel_id, "session_id": m.session_id, "status": m.status, "start": m.start, "end": m.end,
        "duration": round(m.end - m.start, 2), "trigger_time": m.trigger_time, "score": m.score, "priority": m.priority,
        "confidence": m.confidence, "category": m.category, "secondary_categories": m.secondary_categories,
        "subscores": subs.get("values", {}), "points": subs.get("points", {}), "weights": subs.get("weights", {}),
        "reasons": m.reasons, "title": m.title, "hook": m.hook, "transcript": m.transcript, "created_at": m.created_at,
        "thumbnail_url": st.to_media_url(m.thumbnail_path), "source_url": st.to_media_url(m.source_path),
        "source_start": m.source_start, "source_duration": m.source_duration, "model_version": m.model_version,
        "moment_types": [k[5:] for k, v in ((m.signals or {}).get("features") or {}).items() if k.startswith("type_") and v],
        "channel": {"id": channel.id, "display_name": channel.display_name, "platform": channel.platform,
                    "avatar_url": channel.avatar_url} if channel else None,
        "context": {k: (m.context or {}).get(k) for k in ("what", "who", "why", "has_beginning", "has_ending", "standalone", "source")},
    }
    if full:
        out.update(words=m.words, context=m.context, signals=m.signals)
    return out


def clip_out(c: Clip, moment: Moment | None = None, channel: Channel | None = None, full: bool = False) -> dict[str, Any]:
    st = get_storage()
    out = {
        "id": c.id, "moment_id": c.moment_id, "channel_id": c.channel_id, "title": c.title, "status": c.status,
        "review": c.review, "duration": c.duration, "progress": c.progress, "error": c.error,
        "video_url": st.to_media_url(c.output_path), "thumbnail_url": st.to_media_url(c.thumbnail_path) or (
            st.to_media_url(moment.thumbnail_path) if moment else None),
        "created_at": c.created_at, "updated_at": c.updated_at, "render_seconds": c.render_seconds, "render_count": c.render_count,
        "score": moment.score if moment else None, "priority": moment.priority if moment else None,
        "category": moment.category if moment else None,
        "caption_style": (c.edit or {}).get("caption_style", {}).get("style"),
        "channel": {"id": channel.id, "display_name": channel.display_name, "platform": channel.platform,
                    "avatar_url": channel.avatar_url} if channel else None,
    }
    if full:
        out["edit"] = c.edit
        out["source_url"] = st.to_media_url(moment.source_path) if moment else None
        out["moment"] = moment_out(moment, channel, full=True) if moment else None
    return out
