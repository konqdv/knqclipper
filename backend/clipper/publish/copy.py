"""Social copy generator: titles, per-platform descriptions and hashtags for a clip.

Acts as a social-media / SEO strategist (Facebook, TikTok, Instagram, X): it analyses the clip's own
transcript and detected context and writes platform-specific copy. It uses the configured LLM when
available and a content-based heuristic otherwise. Hard rules enforced in both paths:

* never invent facts, quotes, statistics or "trending" claims that aren't supported by the clip,
* never promise guaranteed virality,
* hashtags are derived from the actual content and category, not from fabricated live trends.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from clipper.analysis import lexicon as L
from clipper.analysis.text import norm, shorten
from clipper.llm.providers import LLMError, LLMProvider

log = logging.getLogger(__name__)

PLATFORMS = ["facebook", "tiktok", "instagram", "x", "shorts"]
# how many hashtags each platform comfortably takes
HASHTAG_CAP = {"facebook": 5, "tiktok": 8, "instagram": 12, "x": 3, "shorts": 5}
DESC_LIMIT = {"facebook": 2000, "tiktok": 2000, "instagram": 2100, "x": 270, "shorts": 1500}

_STOP = set("""a al algo ante asi aun bien cada como con contra cual cuando de del desde donde dos el ella ellos
en entre era eres es esa ese eso esta este esto fue gran hay la las le les lo los mas me mi mis mucho muy nada ni
no nos o os otra otro para pero poco por porque que se ser si sin so sobre solo son su sus te ti tu tus un una uno
unos y ya the a an and are as at be but by for from has have he her his i in is it its just like me my no not of on
or our so that this to was we were what when which who will with you your they them их too very can could would should
este esta estamos estoy va van vamos hace hacer dije dijo tan""".split())


@dataclass
class SocialCopy:
    titles: list[dict[str, Any]]
    descriptions: dict[str, list[str]]
    hashtags: dict[str, list[str]]
    recommendations: dict[str, Any]
    source: str = "heuristic"
    language: str = "es"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- keyword / hashtag helpers -----------------------------------------------------------------------
def keywords(text: str, limit: int = 10) -> list[str]:
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-záéíóúñü0-9]{4,}", norm(text)):
        if w in _STOP or w.isdigit():
            continue
        counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


def _tag(word: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", norm(word))


def heuristic_hashtags(category: str, text: str, platform: str) -> list[str]:
    base = {
        "facebook": ["#viral", "#video"],
        "tiktok": ["#fyp", "#parati", "#viral", "#foryou"],
        "instagram": ["#reels", "#reelsinstagram", "#viral", "#explore"],
        "x": ["#viral"],
        "shorts": ["#shorts", "#short"],
    }[platform]
    cat_terms = L.CATEGORY_TERMS.get(category, [])[:4]
    cat_tags = [_tag(t.split()[0]) for t in cat_terms if len(t.split()[0]) > 3]
    content = [_tag(w) for w in keywords(text, 8)]
    out: list[str] = []
    for t in base + cat_tags + content:
        if t not in out and len(t) > 2:
            out.append(t)
    return out[: HASHTAG_CAP[platform]]


# --- heuristic copy ----------------------------------------------------------------------------------
def heuristic_titles(hook: str | None, transcript: str, category: str, lang: str) -> list[dict[str, Any]]:
    src = (hook or transcript or "").strip()
    first = shorten(src, 70)
    cat = L.CATEGORY_LABELS.get(category, category) if hasattr(L, "CATEGORY_LABELS") else category
    opts = []
    if first:
        opts.append(first)
        opts.append(shorten(src, 55).rstrip("… ") + ("…" if len(src) > 55 else ""))
    templates_es = ["Esto fue lo que pasó", "No esperaba esto", "Tenías que verlo", "El momento que se volvió tema"]
    templates_en = ["Here's what happened", "Didn't see that coming", "You had to be there", "The moment everyone's talking about"]
    opts += (templates_en if lang == "en" else templates_es)
    seen, titles = set(), []
    for i, t in enumerate([o for o in opts if o]):
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        titles.append({"text": t, "recommended": i == 0, "reason": "Arranca con lo que de verdad dice el clip." if i == 0 else ""})
        if len(titles) == 5:
            break
    while len(titles) < 5:
        titles.append({"text": cat, "recommended": False, "reason": ""})
    return titles


def heuristic_copy(*, transcript: str, title: str, hook: str | None, category: str, channel: str | None,
                   niche: str | None, country: str | None, lang: str) -> SocialCopy:
    titles = heuristic_titles(hook, transcript or title, category, lang)
    best = titles[0]["text"]
    gist = shorten(transcript or title, 180)
    cta = {"facebook": "¿Qué opinas? Déjalo en los comentarios.", "tiktok": "Guárdalo y cuéntame qué harías tú.",
           "instagram": "Guárdalo si te gustó y comparte con alguien.", "x": "¿Coincides?", "shorts": "Suscríbete para más momentos así."}
    tail = gist if norm(gist) != norm(best) else ""
    descriptions, hashtags = {}, {}
    for p in PLATFORMS:
        intro = best if channel is None else (best if norm(channel) in norm(best) else f"{best} · {channel}")
        d1 = shorten(f"{intro}. {tail}".strip(" ."), DESC_LIMIT[p] - 20).strip() + "."
        d2 = shorten(f"{tail or best}. {cta[p]}".strip(), DESC_LIMIT[p] - 10).strip()
        descriptions[p] = [d1, d2] if norm(d1) != norm(d2) else [d1]
        hashtags[p] = heuristic_hashtags(category, f"{title} {transcript}", p)
    recs = {
        "best_title": best, "best_description": descriptions["instagram"][0],
        "priority_hashtags": hashtags["tiktok"][:5],
        "best_platform": "tiktok" if category in ("entertainment", "gaming", "irl") else "instagram",
        "reach_tip": "Publica cuando tu audiencia está activa y responde a los primeros comentarios para impulsar el alcance.",
        "hook_phrase": shorten(hook or best, 45),
    }
    return SocialCopy(titles, descriptions, hashtags, recs, "heuristic", lang,
                      notes=["Hashtags derivados del contenido; no se consultaron tendencias en vivo."])


# --- LLM copy ----------------------------------------------------------------------------------------
SYSTEM = """Eres un creador de contenido experto en viralización y SEO para redes sociales (Facebook, TikTok, \
Instagram y X). Escribes como una persona real, cercana y natural, nunca como un robot. Reglas estrictas:
- No inventes información, datos, estadísticas ni tendencias que no estén en el vídeo.
- No prometas viralidad garantizada.
- No abuses de emojis ni de hashtags; adapta cada publicación a su plataforma (no copies el mismo texto).
- Hashtags relevantes al contenido y nicho; respeta el límite de caracteres de X.
- Integra el nombre del canal con naturalidad solo cuando encaje.
Devuelve SOLO un objeto JSON."""

USER_TMPL = """Canal: {channel}
Nicho/temática: {niche}
País/público: {country}
Categoría detectada: {category}
Idioma de salida: {lang}
Gancho detectado: {hook}
Transcripción del clip:
\"\"\"{transcript}\"\"\"

Genera este JSON:
{{"titles":[{{"text":str,"recommended":bool,"reason":str}}]  (exactamente 5, uno recommended=true),
 "descriptions":{{"facebook":[str,str],"tiktok":[str,str],"instagram":[str,str],"x":[str,str],"shorts":[str,str]}},
 "hashtags":{{"facebook":[str],"tiktok":[str],"instagram":[str],"x":[str],"shorts":[str]}},
 "recommendations":{{"best_title":str,"best_description":str,"priority_hashtags":[str],"best_platform":str,
   "reach_tip":str,"hook_phrase":str}}}}
Las descripciones deben sonar humanas y específicas de este vídeo. Las de X como máximo 270 caracteres."""


def _clean_hashtags(items: Any, platform: str) -> list[str]:
    out = []
    for h in items or []:
        if not isinstance(h, str):
            continue
        tag = "#" + re.sub(r"[^a-z0-9áéíóúñü]", "", norm(h))
        if len(tag) > 2 and tag not in out:
            out.append(tag)
    return out[: HASHTAG_CAP.get(platform, 8)]


def _sanitize(js: dict[str, Any], fallback: SocialCopy, lang: str) -> SocialCopy:
    titles = []
    for t in (js.get("titles") or [])[:5]:
        if isinstance(t, dict) and t.get("text"):
            titles.append({"text": str(t["text"])[:120], "recommended": bool(t.get("recommended")),
                           "reason": str(t.get("reason") or "")[:200]})
    if not titles:
        titles = fallback.titles
    if not any(t["recommended"] for t in titles):
        titles[0]["recommended"] = True
    desc = {}
    for p in PLATFORMS:
        vals = [str(x)[: DESC_LIMIT[p]] for x in (js.get("descriptions", {}).get(p) or []) if isinstance(x, str) and x.strip()]
        desc[p] = vals[:2] or fallback.descriptions[p]
    tags = {p: (_clean_hashtags(js.get("hashtags", {}).get(p), p) or fallback.hashtags[p]) for p in PLATFORMS}
    r = js.get("recommendations") or {}
    recs = {
        "best_title": str(r.get("best_title") or titles[0]["text"])[:120],
        "best_description": str(r.get("best_description") or desc["instagram"][0])[:300],
        "priority_hashtags": _clean_hashtags(r.get("priority_hashtags"), "tiktok") or tags["tiktok"][:5],
        "best_platform": str(r.get("best_platform") or fallback.recommendations["best_platform"])[:20],
        "reach_tip": str(r.get("reach_tip") or fallback.recommendations["reach_tip"])[:300],
        "hook_phrase": str(r.get("hook_phrase") or fallback.recommendations["hook_phrase"])[:80],
    }
    return SocialCopy(titles, desc, tags, recs, "llm", lang)


async def generate_copy(provider: LLMProvider | None, *, transcript: str, title: str, hook: str | None, category: str,
                        channel: str | None, niche: str | None, country: str | None, lang: str) -> SocialCopy:
    fallback = heuristic_copy(transcript=transcript, title=title, hook=hook, category=category, channel=channel,
                              niche=niche, country=country, lang=lang)
    if provider is None or not (transcript or title):
        return fallback
    prompt = USER_TMPL.format(channel=channel or "(sin nombre)", niche=niche or category, country=country or "(general)",
                              category=category, lang=lang, hook=hook or "(ninguno)", transcript=(transcript or title)[:4000])
    try:
        js = await provider.complete_json(SYSTEM, prompt)
    except LLMError as exc:
        log.info("social copy LLM failed, using heuristic: %s", exc)
        fallback.notes.append(f"llm_failed: {exc}")
        return fallback
    return _sanitize(js, fallback, lang)
