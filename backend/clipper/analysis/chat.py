"""Chat Analyzer: collective audience reactions aligned to stream time.

Chat is a complementary signal: moments are detected without it, chat only adds evidence.
"""

from __future__ import annotations

import re
import threading
from collections import Counter, deque
from dataclasses import asdict, dataclass, field

import numpy as np

from clipper.analysis.lexicon import CHAT_EMOTIONS
from clipper.analysis.text import jaccard, norm
from clipper.connectors.base import ChatMessage

_TOKEN = re.compile(r"[\w?<3:]+|[\U0001F300-\U0001FAFF☀-➿]", re.UNICODE)
_EMO_INDEX = {norm(tok): emo for emo, toks in CHAT_EMOTIONS.items() for tok in toks}


@dataclass
class ChatItem:
    t: float
    user: str
    text: str
    tokens: list[str]
    emotions: list[str]


@dataclass
class ChatSummary:
    messages: int
    rate: float  # msgs/s in window
    baseline_rate: float
    burst_z: float
    repetition: float  # share of messages that are repeated by >= 3 users
    emotions: dict[str, float] = field(default_factory=dict)  # share per collective emotion
    top_messages: list[str] = field(default_factory=list)
    repeated_questions: list[str] = field(default_factory=list)
    unique_users: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def tokenize(text: str) -> list[str]:
    return [norm(t) for t in _TOKEN.findall(text)]


def classify_tokens(tokens: list[str], emotes: list[str]) -> list[str]:
    found = set()
    for tok in tokens + [norm(e) for e in emotes]:
        emo = _EMO_INDEX.get(tok)
        if emo:
            found.add(emo)
        elif re.fullmatch(r"(ja|je|ha|xd){2,}\w*", tok):
            found.add("laugh")
        elif re.fullmatch(r"\?{2,}", tok):
            found.add("surprise")
    return sorted(found)


class ChatAnalyzer:
    def __init__(self, reaction_delay_s: float = 6.0, horizon_s: float = 1800.0) -> None:
        self.reaction_delay_s = reaction_delay_s
        self.horizon_s = horizon_s
        self._items: deque[ChatItem] = deque()
        self._lock = threading.Lock()
        self.wall_start: float | None = None
        self.total = 0

    def to_stream_time(self, wall_ts: float) -> float | None:
        if self.wall_start is None:
            return None
        return wall_ts - self.reaction_delay_s - self.wall_start

    def ingest(self, msg: ChatMessage) -> None:
        t = self.to_stream_time(msg.ts)
        if t is None or t < 0:
            return
        tokens = tokenize(msg.text)
        item = ChatItem(t, msg.user, msg.text, tokens, classify_tokens(tokens, msg.emotes))
        with self._lock:
            self._items.append(item)
            self.total += 1
            while self._items and self._items[0].t < t - self.horizon_s:
                self._items.popleft()

    def _window(self, t0: float, t1: float) -> list[ChatItem]:
        with self._lock:
            return [m for m in self._items if t0 <= m.t < t1]

    def summarize(self, t0: float, t1: float, baseline_s: float = 300.0) -> ChatSummary:
        items = self._window(t0, t1)
        base = self._window(max(0.0, t0 - baseline_s), t0)
        dur = max(1.0, t1 - t0)
        rate = len(items) / dur
        bins = np.zeros(max(1, int(baseline_s // 5)))
        for m in base:
            idx = int((m.t - (t0 - baseline_s)) // 5)
            if 0 <= idx < len(bins):
                bins[idx] += 1
        bins = bins / 5.0
        med = float(np.median(bins)) if len(base) else 0.0
        mad = float(np.median(np.abs(bins - med)) * 1.4826) if len(base) else 0.0
        spread = max(mad, 0.15 * med, 0.2)
        burst_z = (rate - med) / spread if (len(base) >= 10 or rate > 0.5) else 0.0
        texts = Counter(" ".join(m.tokens) for m in items if m.tokens)
        users_per_text: dict[str, set[str]] = {}
        for m in items:
            users_per_text.setdefault(" ".join(m.tokens), set()).add(m.user)
        repeated = sum(c for txt, c in texts.items() if len(users_per_text.get(txt, ())) >= 3)
        emo = Counter(e for m in items for e in m.emotions)
        emotions = {k: round(v / max(1, len(items)), 3) for k, v in emo.items()}
        questions = [m for m in items if m.text.strip().endswith("?") and len(m.tokens) >= 2]
        clusters: list[tuple[set, str, int]] = []
        for q in questions:
            sh = set(q.tokens)
            for i, (csh, txt, n) in enumerate(clusters):
                if jaccard(sh, csh) >= 0.5:
                    clusters[i] = (csh | sh, txt, n + 1)
                    break
            else:
                clusters.append((sh, q.text.strip(), 1))
        rep_q = [txt for _, txt, n in sorted(clusters, key=lambda c: -c[2]) if n >= 3][:3]
        return ChatSummary(
            messages=len(items), rate=round(rate, 3), baseline_rate=round(med, 3), burst_z=round(float(burst_z), 2),
            repetition=round(repeated / max(1, len(items)), 3), emotions=emotions,
            top_messages=[t for t, _ in texts.most_common(5)], repeated_questions=rep_q,
            unique_users=len({m.user for m in items}),
        )

    def quick_trigger(self, t0: float, t1: float) -> tuple[float, list[str]]:
        s = self.summarize(t0, t1)
        if s.messages < 3:
            return 0.0, []
        val = float(1 / (1 + np.exp(-(s.burst_z - 3.0))))
        val *= 0.6 + 0.4 * min(1.0, s.repetition * 2 + max(s.emotions.values(), default=0))
        reasons = []
        if s.burst_z > 2.5:
            top = max(s.emotions, key=s.emotions.get) if s.emotions else None
            reasons.append(f"ráfaga de chat ×{s.burst_z:.1f}" + (f" ({top})" if top else ""))
        return val, reasons
