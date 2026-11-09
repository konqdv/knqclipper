"""YouTube Analytics API v2 client (official, OAuth) for Shorts published on the user's own channel.

TikTok (Display API) and Instagram (Graph API insights) require apps approved by those platforms;
until the user configures such an app, their metrics are imported manually or by CSV.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URL = "https://oauth2.googleapis.com/token"


async def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret,
                                          "refresh_token": refresh_token, "grant_type": "refresh_token"})
        r.raise_for_status()
        return r.json()


async def video_metrics(access_token: str, video_id: str, since: dt.date | None = None) -> dict[str, Any]:
    start = (since or dt.date.today() - dt.timedelta(days=365)).isoformat()
    params = {
        "ids": "channel==MINE", "startDate": start, "endDate": dt.date.today().isoformat(),
        "metrics": "views,averageViewDuration,averageViewPercentage,estimatedMinutesWatched,likes,shares,comments,subscribersGained",
        "filters": f"video=={video_id}",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(ANALYTICS, params=params, headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        js = r.json()
    rows = js.get("rows") or []
    if not rows:
        return {}
    names = [h["name"] for h in js.get("columnHeaders", [])]
    v = dict(zip(names, rows[0]))
    return {
        "views": int(v.get("views", 0)), "avg_retention": float(v.get("averageViewPercentage", 0)),
        "watch_time_s": float(v.get("estimatedMinutesWatched", 0)) * 60, "likes": int(v.get("likes", 0)),
        "shares": int(v.get("shares", 0)), "comments": int(v.get("comments", 0)),
        "followers_gained": int(v.get("subscribersGained", 0)),
    }


def video_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    from clipper.connectors.youtube import YouTubeConnector

    p = YouTubeConnector.parse_url(url)
    return p.handle if p and p.kind == "video" else None
