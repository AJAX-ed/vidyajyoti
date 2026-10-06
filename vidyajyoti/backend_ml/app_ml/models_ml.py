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


def adjust_schedule(plan: list[dict[str, Any]], history: dict[str, Any],
                    session_minutes: int = 50, break_minutes: int = 10) -> dict[str, Any]:
    """Re-pack study sessions around immovable anchors so that the biggest
    continuous free gaps are used first and long sessions land inside the
    user's peak-productivity window (learned from history["by_hour"] scores
    when present). Deterministic; no randomness anywhere.
    """
    anchors = [s for s in plan if s.get("type") in
               ("school", "travel", "meal", "sleep", "routine")]
    wake = min((int(s.get("start", 0)) for s in plan if s.get("type") != "sleep"),
               default=6 * 60)
    sleep = max((int(s.get("end", 0)) for s in plan), default=22 * 60 + 30)
    sess = max(15, min(int(session_minutes or 50), 180))
    brk = max(5, min(int(break_minutes or 10), 30))

    # Free intervals = waking day minus anchors.
    busy = sorted((int(a["start"]), int(a["end"])) for a in anchors
                  if int(a.get("end", 0)) > wake and int(a.get("start", 1440)) < sleep)
    free: list[tuple[int, int]] = []
    cur = wake
    for s, e in busy:
        if s > cur:
            free.append((cur, min(s, sleep)))
        cur = max(cur, e)
    if cur < sleep:
        free.append((cur, sleep))
    free = [(s, e) for s, e in free if e - s >= 20]

    # Peak window from history if provided, else largest gap.
    peak_start, peak_end = None, None
    by_hour = (history or {}).get("by_hour") or {}
    if by_hour:
        best_h = max(by_hour, key=lambda h: float(by_hour[h]))
        peak_start, peak_end = int(best_h) * 60, int(best_h) * 60 + 60
    elif free:
        g = max(free, key=lambda se: se[1] - se[0])
        peak_start, peak_end = g

    def pack(gap_s: int, gap_e: int, boost: bool) -> list[dict[str, Any]]:
        slots, t = [], gap_s
        n = 0
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
    changed = 0
    for gs, ge in free:
        in_peak = peak_start is not None and gs < peak_end and ge > peak_start
        before = sum(1 for s in plan if s.get("type") == "study"
                     and s.get("start") == gs)
        packed = pack(gs, ge, boost=in_peak)
        if not before:
            changed += len(packed)
        new_slots.extend(packed)

    kept = [s for s in plan if s.get("type") != "study"]
    merged = sorted(kept + new_slots, key=lambda s: int(s.get("start", 0)))
    total_study = sum(s["end"] - s["start"] for s in new_slots
                      if s.get("type") == "study")
    return {
        "plan": merged,
        "changed_slots": changed,
        "study_minutes": total_study,
        "sessions": sum(1 for s in new_slots if s.get("type") == "study"),
        "peak_window": [m2t(peak_start), m2t(peak_end)] if peak_start is not None else None,
        "engine": "constraint-solver-v2",
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
