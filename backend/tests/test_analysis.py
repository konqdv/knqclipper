"""Text, hook, story, boundaries, chat, classifier and ranker."""

from __future__ import annotations

import time

import numpy as np
import pytest

from clipper.analysis.chat import ChatAnalyzer
from clipper.analysis.classifier import ContentClassifier, map_platform_category
from clipper.analysis.context import ContextAnalyzer, _verbatim
from clipper.analysis.hook import score_sentence
from clipper.analysis.ranker import HighlightRanker, effective_weights, priority_for, temporal_iou, text_similarity
from clipper.analysis.story import StoryDetector
from clipper.analysis.text import segment_sentences
from clipper.clips.boundaries import choose_sentence_bounds, snap_times
from clipper.connectors.base import ChatMessage
from clipper.core.appsettings import DEFAULTS
from tests.conftest import make_words

STORY = ("I like coffee in the morning. Anyway the weather is fine today. So one time I was working at a restaurant. "
         "And then the owner asked me to cook for a famous chef. But then suddenly the kitchen caught fire! "
         "In the end the chef loved the bread and offered me a job. That is how I became a cook. "
         "Now let us talk about something else entirely. The traffic was bad.")


def test_sentence_segmentation_uses_punctuation_pauses_and_speakers():
    words = make_words("Hello there. How are you? Fine thanks") + make_words("Great to hear.", start=20, spk="S2")
    sents = segment_sentences(words)
    assert [s.text for s in sents] == ["Hello there.", "How are you?", "Fine thanks", "Great to hear."]
    assert sents[1].is_question and sents[2].pause_after > 5


def test_hook_scores_attention_grabbers_higher():
    sents = segment_sentences(make_words("You won't believe what happened to me. I bought some bread today. And then it was fine."))
    hook, plain, dependent = (score_sentence(s) for s in sents)
    assert hook.score > plain.score
    assert "curiosity" in hook.types
    assert dependent.needs_context and not hook.needs_context


def test_story_detector_finds_arc_and_bounds():
    sents = segment_sentences(make_words(STORY))
    arc = StoryDetector().detect(sents)
    assert arc.score >= 0.7
    assert sents[arc.start_index].text.startswith("So one time")
    assert "That is how" in sents[arc.end_index].text or "In the end" in sents[arc.end_index].text


def test_boundaries_start_at_story_intro_and_never_cut_words():
    words = make_words(STORY)
    sents = segment_sentences(words)
    arc = StoryDetector().detect(sents)
    fire = next(s for s in sents if "fire" in s.text)
    i0, i1, _ = choose_sentence_bounds(sents, fire.end, min_d=8, max_d=60, story=arc)
    assert sents[i0].text.startswith("So one time")
    assert "fire" in " ".join(s.text for s in sents[i0: i1 + 1])
    start, end = snap_times(words, sents, i0, i1)
    for w in words:  # no word may straddle a cut point
        assert not (w.s < start < w.e) and not (w.s < end < w.e)


def test_boundaries_respect_max_duration_and_learned_preference():
    words = make_words(STORY, wps=1.0)
    sents = segment_sentences(words)
    anchor = next(s for s in sents if "fire" in s.text).end
    i0, i1, _ = choose_sentence_bounds(sents, anchor, min_d=5, max_d=30)
    assert sents[i1].end - sents[i0].start <= 30
    short = choose_sentence_bounds(sents, anchor, min_d=5, max_d=90, pref=(8, 14))
    long_ = choose_sentence_bounds(sents, anchor, min_d=5, max_d=90, pref=(50, 80))
    dur = lambda b: sents[b[1]].end - sents[b[0]].start  # noqa: E731
    assert dur(short) < dur(long_)


def test_chat_burst_emotions_and_repeated_questions():
    ca = ChatAnalyzer(reaction_delay_s=0)
    ca.wall_start = 1000.0
    for i in range(0, 120, 10):  # quiet baseline: one msg every 10 s
        ca.ingest(ChatMessage(1000 + i, f"u{i}", "hello", "twitch"))
    for j in range(40):  # burst at t=130..135
        txt = "KEKW" if j % 3 else "what did he say?"
        ca.ingest(ChatMessage(1130 + j * 0.12, f"v{j}", txt, "twitch"))
    s = ca.summarize(129, 136)
    assert s.burst_z > 5 and s.emotions.get("laugh", 0) > 0.5
    assert s.repetition > 0.5 and s.repeated_questions
    val, reasons = ca.quick_trigger(130, 136)
    assert val > 0.7 and reasons


def test_classifier_uses_platform_and_transcript_with_hysteresis():
    assert map_platform_category("Just Chatting") == "entertainment"
    assert map_platform_category("Elden Ring") == "gaming"
    c = ContentClassifier("Talk Shows & Podcasts")
    for _ in range(5):
        res = c.update("welcome to the podcast our guest today has an interesting question for the interview")
    assert res.primary == "podcast"
    for _ in range(2):  # a brief switch is not enough
        res = c.update("the recipe needs the oven and two ingredients salt and flour bake it")
    assert res.primary == "podcast"
    for _ in range(25):
        res = c.update("the recipe needs the oven and the ingredients salt flour bake cook taste recipe oven")
    assert res.primary == "cooking"


def test_ranker_weights_sum_to_100_and_adapt_to_category():
    base = DEFAULTS["ranker"]["weights"]
    for cat in ("education", "gaming", "podcast", "music", None):
        assert sum(effective_weights(base, cat).values()) == pytest.approx(100)
    subs = {"hook": 0.7, "entertainment": 0.0, "narrative": 0.6, "emotion": 0.1, "flow": 0.9, "shareability": 0.9, "audio_quality": 0.9}
    r = HighlightRanker(DEFAULTS["ranker"])
    edu = r.rank(subs, category="education").score
    gam = r.rank(subs, category="gaming").score
    assert edu > gam + 5, "an explanatory clip must not be penalised for having no laughs in an educational stream"
    assert priority_for(91, DEFAULTS["ranker"]["tiers"]) == "excellent" and priority_for(59, DEFAULTS["ranker"]["tiers"]) == "low"


def test_learned_multipliers_are_bounded():
    w = effective_weights(DEFAULTS["ranker"]["weights"], None, learned={"hook": 99.0, "emotion": 0.0})
    assert sum(w.values()) == pytest.approx(100)
    assert w["hook"] / DEFAULTS["ranker"]["weights"]["hook"] < 2.0


def test_dedup_metrics():
    assert temporal_iou((0, 10), (5, 15)) == pytest.approx(5 / 15)
    assert text_similarity("the kitchen caught fire and everybody ran", "the kitchen caught fire and everybody ran outside") > 0.6


def test_context_validation_rejects_fabricated_quotes():
    sents = segment_sentences(make_words(STORY))
    assert _verbatim("the kitchen caught fire", sents)
    assert _verbatim("the chef stole my car", sents) is None
    ca = ContextAnalyzer(None)
    base = ca.heuristic(sents, 2, 6, emotion=None, chat=None, visual=None, story=None, hook=None, classification=None, reasons=[], lang="en")
    out = ca._validate({"best_start_sentence": 2, "best_end_sentence": 99, "hook_quote": "the chef stole my car",
                        "title": 'He said "I am the king"', "scores": {"hook": 0.9}}, base, sents)
    assert out.best_end == base.best_end and out.hook_quote == base.hook_quote
    assert "king" not in out.title and any("fabricated" in n for n in out.notes)


time  # noqa: B018
np  # noqa: B018


def test_lexical_reasons_are_collapsed():
    from clipper.analysis.engine import merge_reasons

    out = merge_reasons(["risas", "frase llamativa (story)"], ["frase llamativa (question, story)", "risas"])
    assert out == ["risas", "frases llamativas: story, question"]


def test_uninformative_llm_scores_are_ignored():
    sents = segment_sentences(make_words(STORY))
    ca = ContextAnalyzer(None)
    base = ca.heuristic(sents, 2, 6, emotion=None, chat=None, visual=None, story=None, hook=None, classification=None, reasons=[], lang="en")
    flat = {k: 1.0 for k in ("hook", "entertainment", "narrative", "emotion", "flow", "shareability")}
    out = ca._validate({"best_start_sentence": 2, "best_end_sentence": 6, "scores": flat}, base, sents)
    assert out.llm_scores is None and any("uninformative" in n for n in out.notes)
    varied = {**flat, "emotion": 0.3}
    assert ca._validate({"best_start_sentence": 2, "best_end_sentence": 6, "scores": varied}, base, sents).llm_scores == varied
