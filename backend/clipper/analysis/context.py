"""Context Analyzer: answers the eight context questions for a candidate window.

1. What is happening?   2. Who is speaking?   3. What is the context?   4. What makes it interesting?
5. Does it have a beginning and an ending?   6. Is it understandable without the original stream?
7. Does it entertain, surprise or inform?    8. Best start and end point?

With an LLM configured the answers are semantic; without one they are derived from the multimodal
signals. In both cases the output is *validated against the real transcript*: quotes must be
verbatim, boundaries must be existing sentence indices, nothing invented survives.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from clipper.analysis import lexicon as L
from clipper.analysis.text import Sentence, count_cues, norm, shorten, starts_with_anaphora
from clipper.llm.providers import LLMError, LLMProvider

log = logging.getLogger(__name__)

CRITERIA = ["hook", "entertainment", "narrative", "emotion", "flow", "shareability"]

SYSTEM_PROMPT = """You are the editorial brain of a live-stream clipping tool that works for ANY kind of content \
(podcasts, interviews, debates, IRL, gaming, education, news, sports, cooking, music...).
You receive numbered transcript sentences with timestamps and speaker labels, plus detected signals.
Decide whether this window contains a moment worth turning into a short vertical clip, and where it should start and end.
Rules:
- Never invent words. Any quote you return must be copied verbatim from the transcript.
- Never choose boundaries that change the meaning of what was said or cut a conclusion.
- Prefer starting as close as possible to the interesting part, but include the context needed to understand it.
- A laugh or a shout is not interesting by itself: identify what caused it.
- Judge the moment by the standards of its content type (an educational explanation does not need laughs).
Return JSON only."""

USER_TEMPLATE = """Content category (auto-detected): {category} (secondary: {secondary})
Platform category: {platform_category}
Detected signals: {signals}
Trigger time: {trigger:.1f}s — reasons: {reasons}

Transcript sentences:
{sentences}

Answer with this JSON schema:
{{"what_is_happening": str, "who_is_speaking": str, "context": str, "why_interesting": str,
 "has_beginning": bool, "has_ending": bool, "standalone": bool,
 "potential": {{"entertain": 0-1, "surprise": 0-1, "inform": 0-1}},
 "best_start_sentence": int, "best_end_sentence": int,
 "hook_quote": "verbatim sentence or fragment that works as the opening hook, or empty",
 "title": "short catchy title in the transcript's language, no fabricated quotes",
 "moment_types": [one or more of: humor, reaction, story, revelation, controversy, opinion, conflict, tension,
                  useful_tip, surprising_fact, play, fail, interaction, absurd, challenge, confession, question],
 "scores": {{"hook": 0-1, "entertainment": 0-1, "narrative": 0-1, "emotion": 0-1, "flow": 0-1, "shareability": 0-1}},
 "worth_clipping": bool, "confidence": 0-1}}"""


@dataclass
class ContextAnalysis:
    what: str
    who: str
    context: str
    why: str
    has_beginning: bool
    has_ending: bool
    standalone: bool
    potential: dict[str, float]
    best_start: int
    best_end: int
    hook_quote: str | None
    title: str
    moment_types: list[str]
    llm_scores: dict[str, float] | None = None
    worth_clipping: bool = True
    source: str = "heuristic"
    confidence: float = 0.5
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _verbatim(quote: str, sentences: list[Sentence]) -> str | None:
    q = norm(quote).strip(" \"'«»“”.,!?¡¿")
    if len(q) < 4:
        return None
    for s in sentences:
        if q in norm(s.text):
            # return the original-cased substring from the transcript
            idx = norm(s.text).find(q)
            return s.text[idx: idx + len(q)] if len(norm(s.text)) == len(s.text) else s.text
    full = " ".join(s.text for s in sentences)
    return full if q in norm(full) and len(q) < 200 else None


def moment_types_from_cues(sentences: list[Sentence], emotion, chat, visual, story, category: str) -> list[str]:
    text = " ".join(s.text for s in sentences)
    types: list[str] = []
    ev = {e.type for e in (emotion.events if emotion else [])}
    chat_emo = (chat.emotions if chat else {}) or {}
    if "laughter" in ev or count_cues(text, L.HUMOR) or chat_emo.get("laugh", 0) > 0.15:
        types.append("humor")
    if ev & {"shout", "surprise"} or chat_emo.get("surprise", 0) > 0.15 or chat_emo.get("shock", 0) > 0.1:
        types.append("reaction")
    if story and story.score >= 0.5:
        types.append("story")
    if count_cues(text, L.HOOK["revelation"]):
        types.append("revelation")
    if count_cues(text, L.HOOK["controversy"]):
        types.append("controversy")
    if count_cues(text, L.HOOK["conflict"]) or "anger" in ev:
        types.append("conflict")
    if count_cues(text, L.OPINION) >= 2:
        types.append("opinion")
    if count_cues(text, L.VALUE) >= 2:
        types.append("useful_tip")
    if count_cues(text, L.HOOK["tension"]) or chat_emo.get("scare", 0) > 0.1:
        types.append("tension")
    if category == "gaming" and (count_cues(text, L.GAMING_EVENTS) or (visual and visual.motion_peak_z > 4)):
        types.append("play")
    if visual and visual.people_change:
        types.append("interaction")
    if any(s.is_question for s in sentences) and "story" not in types:
        types.append("question")
    return types or ["conversation"]


def heuristic_title(hook_text: str | None, sentences: list[Sentence], types: list[str], lang: str | None) -> str:
    src = hook_text or (max(sentences, key=lambda s: len(s.text)).text if sentences else "")
    quote = shorten(src, 64).strip()
    if not quote:
        return {"es": "Momento destacado"}.get(lang or "", "Highlight")
    return quote


class ContextAnalyzer:
    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.llm = llm

    def heuristic(self, sentences: list[Sentence], start: int, end: int, *, emotion, chat, visual, story, hook,
                  classification, reasons: list[str], lang: str | None) -> ContextAnalysis:
        sel = sentences[start: end + 1] if sentences else []
        speakers = sorted({s.speaker for s in sel if s.speaker})
        types = moment_types_from_cues(sel, emotion, chat, visual, story, classification.primary if classification else "")
        needs_ctx = bool(sel) and starts_with_anaphora(sel[0].text, L.ANAPHORA_STARTERS)
        has_end = bool(sel) and (sel[-1].text.rstrip()[-1:] in ".!?…" or sel[-1].pause_after > 0.8)
        has_begin = bool(sel) and (not needs_ctx) and (sel[0].pause_before > 0.4 or (story and story.start_index == start))
        text = " ".join(s.text for s in sel)
        inform = min(1.0, count_cues(text, L.VALUE) / 4)
        entertain = min(1.0, (("humor" in types) * 0.6 + ("reaction" in types) * 0.3 + ("story" in types) * 0.3))
        surprise = min(1.0, count_cues(text, L.HOOK["surprise"]) / 2 + ("reaction" in types) * 0.4)
        who = (f"{len(speakers)} voces ({', '.join(speakers)})" if len(speakers) > 1 else "un hablante principal") if sel else "sin voz"
        what = f"{classification.primary if classification else 'stream'} · " + ", ".join(types)
        hook_text = hook.text if hook and hook.score > 0.3 else None
        return ContextAnalysis(
            what=what, who=who, context=shorten(sentences[max(0, start - 1)].text, 140) if sentences and start > 0 else "",
            why="; ".join(reasons) or ", ".join(types), has_beginning=bool(has_begin), has_ending=bool(has_end),
            standalone=bool(sel) and not needs_ctx and has_end,
            potential={"entertain": round(entertain, 2), "surprise": round(surprise, 2), "inform": round(inform, 2)},
            best_start=start, best_end=end, hook_quote=hook_text, title=heuristic_title(hook_text, sel, types, lang),
            moment_types=types, source="heuristic", confidence=0.45,
        )

    async def analyze(self, sentences: list[Sentence], start: int, end: int, *, emotion, chat, visual, story, hook,
                      classification, reasons: list[str], lang: str | None, platform_category: str | None,
                      trigger: float, images: list[bytes] | None = None) -> ContextAnalysis:
        base = self.heuristic(sentences, start, end, emotion=emotion, chat=chat, visual=visual, story=story, hook=hook,
                              classification=classification, reasons=reasons, lang=lang)
        if self.llm is None or not sentences:
            return base
        lines = "\n".join(f"[{i}] ({s.start:.1f}-{s.end:.1f}s{', ' + s.speaker if s.speaker else ''}) {s.text}" for i, s in enumerate(sentences))
        sig = {
            "vocal_events": [f"{e.type}@{e.t:.1f}s" for e in (emotion.events if emotion else [])][:12],
            "peak_arousal": round(emotion.arousal, 2) if emotion else None,
            "chat": {"burst_z": chat.burst_z, "emotions": chat.emotions, "top": chat.top_messages[:3]} if chat and chat.messages else None,
            "visual": {"faces": round(visual.faces_mean, 1), "events": visual.events[:6]} if visual and visual.frames else None,
            "heuristic_bounds": [start, end],
        }
        prompt = USER_TEMPLATE.format(
            category=classification.primary if classification else "unknown",
            secondary=", ".join(classification.secondary) if classification else "",
            platform_category=platform_category or "n/a", signals=sig, trigger=trigger, reasons=", ".join(reasons) or "periodic scan",
            sentences=lines,
        )
        try:
            js = await self.llm.complete_json(SYSTEM_PROMPT, prompt, images)
        except LLMError as exc:
            log.warning("LLM context analysis failed, using heuristic: %s", exc)
            base.notes.append(f"llm_failed: {exc}")
            return base
        return self._validate(js, base, sentences)

    def _validate(self, js: dict[str, Any], base: ContextAnalysis, sentences: list[Sentence]) -> ContextAnalysis:
        notes: list[str] = []
        n = len(sentences)

        def idx(key: str, default: int) -> int:
            try:
                v = int(js.get(key))
                if 0 <= v < n:
                    return v
                notes.append(f"{key} out of range")
            except (TypeError, ValueError):
                notes.append(f"{key} invalid")
            return default

        s0, s1 = idx("best_start_sentence", base.best_start), idx("best_end_sentence", base.best_end)
        if s1 < s0:
            s0, s1 = base.best_start, base.best_end
            notes.append("inverted bounds ignored")
        quote = js.get("hook_quote") or ""
        hook_quote = _verbatim(quote, sentences[s0: s1 + 1]) if quote else None
        if quote and hook_quote is None:
            notes.append("hook_quote not verbatim → discarded")
            hook_quote = base.hook_quote
        title = str(js.get("title") or "").strip()[:90]
        for q in re.findall(r"[\"«“]([^\"»”]+)[\"»”]", title):
            if _verbatim(q, sentences) is None:
                notes.append("title contained a fabricated quote → replaced")
                title = ""
                break
        scores = js.get("scores") or {}
        llm_scores = {}
        for c in CRITERIA:
            try:
                llm_scores[c] = float(min(1.0, max(0.0, float(scores.get(c)))))
            except (TypeError, ValueError):
                pass
        pot = js.get("potential") or {}
        types = [t for t in (js.get("moment_types") or []) if isinstance(t, str)][:6] or base.moment_types
        try:
            conf = float(js.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        return ContextAnalysis(
            what=str(js.get("what_is_happening") or base.what)[:400], who=str(js.get("who_is_speaking") or base.who)[:200],
            context=str(js.get("context") or base.context)[:400], why=str(js.get("why_interesting") or base.why)[:400],
            has_beginning=bool(js.get("has_beginning", base.has_beginning)), has_ending=bool(js.get("has_ending", base.has_ending)),
            standalone=bool(js.get("standalone", base.standalone)),
            potential={k: float(min(1, max(0, float(pot.get(k, base.potential.get(k, 0)) or 0)))) for k in ("entertain", "surprise", "inform")},
            best_start=s0, best_end=s1, hook_quote=hook_quote, title=title or base.title, moment_types=types,
            llm_scores=llm_scores or None, worth_clipping=bool(js.get("worth_clipping", True)), source="llm",
            confidence=max(0.0, min(1.0, conf)), notes=notes,
        )
