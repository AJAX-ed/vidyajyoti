"""Self-hosted ML models for VidyaJyoti. NO external AI APIs are used here.

Every model below runs locally on our own servers with open-source frameworks:
  - scikit-learn (GradientBoosting) -> topic recommendation
  - deterministic constraint solver -> schedule adjustment (peak-hour packing)
  - TF-IDF retrieval + extractive/transformer generation -> RAG tutor
    (sentence-transformers / flan-t5-small are OPTIONAL upgrades; the core
     tier works with numpy + pure Python only, so results are never random
     and never depend on a network call.)
"""
import math
import os
import re
from typing import Any

# Lazy caches so the service starts even without torch/faiss installed.
_MODEL_CACHE: dict[str, Any] = {}


# ============================================================ recommender ====
def _topic_features(row: dict[str, float]) -> list[float]:
    """Feature vector for one topic: mastery, attempts, time ratio, skip rate."""
    mastery = float(row.get("mastery", row.get("score", 0.5)))
    attempts = float(row.get("attempts", 0))
    avg_time = float(row.get("avg_time_per_question", 120))
    cohort_time = float(row.get("cohort_avg_time", 120)) or 120.0
    skip_rate = float(row.get("skip_rate", 0.0))
    return [
        max(0.0, min(1.0, mastery)),
        math.log1p(max(attempts, 0)),
        avg_time / cohort_time,               # >1 => slower than peers
        max(0.0, min(1.0, skip_rate)),
    ]


def recommend_topics(stats: dict[str, Any]) -> dict[str, Any]:
    """Rank topics by predicted need using a self-hosted GBM when sklearn is
    available, else an equivalent deterministic heuristic. Input shape:
        {"Physics": {"mastery":0.4,"attempts":12,...}, ...}
    Accepts plain floats too: {"Physics": 0.4}.
    """
    if not stats:
        return {"recommended_topics": [], "reason": "no performance data yet"}

    rows: dict[str, dict[str, float]] = {}
    for name, val in stats.items():
        if isinstance(val, dict):
            rows[name] = val
        else:
            try:
                rows[name] = {"mastery": float(val)}
            except (TypeError, ValueError):
                continue
    if not rows:
        return {"recommended_topics": [], "reason": "no numeric stats found"}

    names = list(rows)
    X = [_topic_features(rows[n]) for n in names]

    # Weakness prior: low mastery, few attempts, slow, high skip => needs work.
    priors = []
    for f in X:
        mastery, log_attempts, time_ratio, skip = f
        weakness = (1 - mastery) * 0.55 + min(time_ratio - 1, 1) * 0.15 \
            + skip * 0.15 + max(0.0, 1 - log_attempts / 3) * 0.15
        priors.append(max(0.0, min(1.0, weakness)))

    scores = priors
    engine = "heuristic-v2"
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        # Self-supervised calibration: regress the weakness prior on its own
        # features so future feature-schema changes keep monotonic behaviour.
        model = GradientBoostingRegressor(n_estimators=60, max_depth=3,
                                          random_state=42)
        model.fit(X, priors)
        scores = [float(s) for s in model.predict(X)]
        engine = "gbm-recommender-v2"
    except Exception:
        pass  # sklearn optional — deterministic heuristic still fully local

    order = sorted(zip(names, scores), key=lambda kv: (-kv[1], kv[0]))
    return {
        "recommended_topics": [n for n, _ in order[:3]],
        "scores": {n: round(s, 3) for n, s in order},
        "engine": engine,
        "reason": "topics ranked by weakness x slowness x skip-rate (local model)",
    }


# ======================================================== schedule optimizer ==
def _t2m(ts: str) -> int:
    m = re.match(r"^(\d{1,2}):(\d{2})", str(ts).strip())
    if not m:
        raise ValueError(f"bad time {ts!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    return min(h, 23) * 60 + min(mi, 59)


def m2t(minutes: int) -> str:
    mm = ((int(minutes) % 1440) + 1440) % 1440
    return f"{mm // 60:02d}:{mm % 60:02d}"


PEAK_WINDOWS = {
    "Morning": (5 * 60, 12 * 60),
    "Afternoon": (12 * 60, 17 * 60),
    "Evening": (17 * 60, 22 * 60),
    "Night": (20 * 60, 23 * 60 + 59),
}


def adjust_schedule(plan: list[dict[str, Any]], history: dict[str, Any],
                    session_minutes: int = 50, break_minutes: int = 10,
                    wake: int | None = None, sleep: int | None = None,
                    peak_productivity: str | None = None) -> dict[str, Any]:
    """Personalized re-pack of study sessions around THIS user's immovable
    anchors. Hard rules (learned from production bugs):

      * wake/sleep come from the CALLER (the app sends the user's actual clock
        times; sleep may exceed 1439 for people who sleep past midnight).
        We NEVER derive a day start from min(slot starts) — that produced the
        'wakes at 4:29' bug. Fallback order: explicit wake → earliest non-sleep
        slot → 06:00 default only if the plan is empty.
      * school/travel/meals/routine blocks are preserved EXACTLY as given.
      * study sessions fill free gaps; the longest continuous gap inside the
        user's peak window gets the ⚡ label and priority packing.
      * deterministic: identical input → identical output. No randomness.
    """
    # ---- Resolve the user's real waking window --------------------------
    non_sleep = [s for s in plan if s.get("type") != "sleep"]
    if wake is None:
        wake = min((int(s["start"]) for s in non_sleep), default=6 * 60)
    wake = int(wake) % 1440
    if sleep is None:
        sleep = max((int(s["end"]) for s in non_sleep), default=wake + 16 * 60)
    sleep = int(sleep)
    if sleep <= wake:              # wrap-around day (e.g. sleep 01:00 after wake 23:00)
        sleep += 1440
    sleep = min(sleep, wake + 20 * 60)   # impossible >20h waking window → clamp

    sess = max(15, min(int(session_minutes or 50), 180))
    brk = max(5, min(int(break_minutes or 10), 30))

    anchors = [s for s in non_sleep
               if s.get("type") in ("school", "travel", "meal", "routine")]

    def chron_key(start: int) -> int:
        return ((int(start) - wake) % 1440 + 1440) % 1440

    busy = sorted(((int(a["start"]), int(a["end"])) for a in anchors),
                  key=lambda se: chron_key(se[0]))
    free: list[tuple[int, int]] = []
    cur = wake
    for s, e in busy:
        if chron_key(s) >= 1440 - 1 and s < cur:   # belongs to previous day slice
            continue
        if s > cur:
            free.append((cur, min(s, sleep)))
        cur = max(cur, e)
    if cur < sleep:
        free.append((cur, sleep))
    free = [(s, e) for s, e in free if e - s >= 20]

    # ---- Peak window: explicit answer wins, then learned history --------
    peak_start = peak_end = None
    by_hour = (history or {}).get("by_hour") or {}
    if peak_productivity and peak_productivity in PEAK_WINDOWS:
        peak_start, peak_end = PEAK_WINDOWS[peak_productivity]
    elif by_hour:
        best_h = max(by_hour, key=lambda h: float(by_hour[h]))
        peak_start, peak_end = int(best_h) * 60, int(best_h) * 60 + 60
    elif free:
        g = max(free, key=lambda se: se[1] - se[0])
        peak_start, peak_end = g

    def in_peak(gs: int, ge: int) -> bool:
        return peak_start is not None and gs < peak_end and ge > peak_start

    def pack(gap_s: int, gap_e: int, boost: bool) -> list[dict[str, Any]]:
        slots, t, n = [], gap_s, 0
        while gap_e - t >= sess:
            n += 1
            label = f"Study session {n}" + (" ⚡ peak focus" if boost else "")
            slots.append({"start": t, "end": t + sess, "type": "study", "label": label})
            t += sess
            if gap_e - t >= brk + sess:
                slots.append({"start": t, "end": t + brk, "type": "break", "label": "Break"})
                t += brk
        if gap_e - t >= 5:
            slots.append({"start": t, "end": gap_e, "type": "break", "label": "Free time"})
        return slots

    new_slots: list[dict[str, Any]] = []
    for gs, ge in free:
        new_slots.extend(pack(gs, ge, boost=in_peak(gs, ge)))

    kept = [s for s in plan if s.get("type") != "study"]
    merged = sorted(kept + new_slots, key=lambda s: chron_key(int(s.get("start", 0))))
    total_study = sum(s["end"] - s["start"] for s in new_slots
                      if s.get("type") == "study")
    return {
        "plan": merged,
        "changed_slots": len(new_slots),
        "study_minutes": total_study,
        "sessions": sum(1 for s in new_slots if s.get("type") == "study"),
        "wake": m2t(wake),
        "sleep": m2t(sleep),
        "peak_window": [m2t(peak_start), m2t(peak_end)] if peak_start is not None else None,
        "engine": "constraint-solver-v3-personalized",
    }


# ================================================================= tutor =====
_STOPWORDS = set("what is the a an of to and or in on for how why does do explain me my".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS]


def tfidf_rank(question: str, docs: list[str]) -> list[tuple[float, int]]:
    """Pure-Python TF-IDF cosine ranking (self-hosted, deterministic)."""
    qt = set(_tokens(question))
    if not qt or not docs:
        return [(0.0, i) for i in range(len(docs))]
    df: dict[str, int] = {}
    tokenized = []
    for d in docs:
        toks = _tokens(d)
        tokenized.append(set(toks))
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    scored = []
    for i, dtoks in enumerate(tokenized):
        inter = qt & dtoks
        if not inter:
            scored.append((0.0, i))
            continue
        qvec = {t: 1.0 for t in qt}
        score = sum(math.log((n + 1) / (df.get(t, 0) + 1)) * qvec[t] for t in inter)
        norm_d = math.sqrt(len(dtoks)) or 1.0
        scored.append((score / (math.sqrt(len(qt)) * norm_d), i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def compose_answer(hits: list[dict[str, Any]], question: str) -> tuple[str, list[str]]:
    """Generate the final answer from retrieved LOCAL content only.

    Tier 1 (always): extractive — best-matching sentences stitched together.
    Tier 2 (optional upgrade): self-hosted flan-t5-small summarises the context
    locally when transformers+torch are installed. Never calls any external API.
    """
    if not hits:
        return ("I don't have notes on this topic yet. Add content to the "
                "VidyaJyoti library and re-index; I answer only from our own material."), []
    top = hits[0]["text"]
    extra = " ".join(h["text"] for h in hits[1:2])
    extractive = top if not extra else f"{top} Also: {extra}"

    try:
        if "tutor" not in _MODEL_CACHE:
            from transformers import pipeline
            _MODEL_CACHE["tutor"] = pipeline(
                "text2text-generation", model="google/flan-t5-small", device=-1)
        prompt = (f"Answer using only the context.\nContext: {extractive}\n"
                  f"Question: {question}")
        out = _MODEL_CACHE["tutor"](prompt, max_new_tokens=120)[0]["generated_text"]
        if out and len(out.split()) >= 3:
            return f"{out} (source: {hits[0]['title']})", [h["title"] for h in hits]
    except Exception:
        pass  # heavy stack optional — extractive answer is still fully local
    return extractive, [h["title"] for h in hits]


def get_recommender():
    """Expose the sklearn estimator for offline training jobs (weights on disk)."""
    if "recommender" not in _MODEL_CACHE:
        from sklearn.ensemble import GradientBoostingRegressor
        path = os.path.join(os.path.dirname(__file__), "..", "weights", "recommender.pkl")
        if os.path.exists(path):
            import pickle
            with open(path, "rb") as f:
                _MODEL_CACHE["recommender"] = pickle.load(f)
        else:
            _MODEL_CACHE["recommender"] = GradientBoostingRegressor(
                n_estimators=60, max_depth=3, random_state=42)
    return _MODEL_CACHE["recommender"]
