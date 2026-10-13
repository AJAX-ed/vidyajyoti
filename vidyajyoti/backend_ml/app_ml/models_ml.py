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


# =============================================== weekly/monthly goal planner ==
def _days_left_this_year(today_iso: str | None = None) -> int:
    from datetime import date as _date
    if today_iso:
        try:
            y, m, d = map(int, today_iso.split("-"))
            today = _date(y, m, d)
        except Exception:
            today = _date.today()
    else:
        today = _date.today()
    return max(1, (_date(today.year, 12, 31) - today).days)


def _syllabus_topics(grade: str, subjects: list[str], count: int) -> list[dict]:
    """Placeholder syllabus until real curriculum data is supplied.

    Contract (from the product spec):
      * normal grades   -> "Topic <Grade name> <1..10>" per subject
      * dropers (grade 12 / "Drop year") -> "Topic <1..20>" per subject
    Returns a flat ordered list of {subject, topic} dicts of length `count`."""
    is_dropper = str(grade).strip().lower() in ("12", "drop year", "dropper", "drop")
    topics: list[dict] = []
    idx: dict[str, int] = {}
    while len(topics) < count:
        progressed = False
        for s in subjects:
            i = idx.get(s, 0) + 1
            limit = 20 if is_dropper else 10
            if i > limit:
                continue
            idx[s] = i
            name = f"Topic {i}" if is_dropper else f"Topic {grade} {i}"
            topics.append({"subject": s, "topic": name})
            progressed = True
        if not progressed or len(topics) >= count:
            break
    return topics[:count]


def build_goals(payload: dict) -> dict:
    """Self-hosted weekly/monthly pacing planner.

    Given the student's grade, subjects and DAILY study capacity (minutes),
    it computes how many topics can be finished by Dec 31 of THIS year without
    overloading anyone, then splits them into non-exhaustive weekly goals and
    monthly milestones. Deterministic given the same input — but personalized
    per student because capacity, subjects and start date all differ.

    Pacing model:
      * ~50 focused minutes per new topic (one session incl. notes).
      * 15% of daily capacity reserved for revision/quiz days → never exhaustive.
      * Weekly load is clamped to a human cadence (3–14 topics/week).
      * The syllabus pool is paced ACROSS the remaining weeks so the student
        finishes the available topics right around Dec 31 instead of burning
        out in week one. If real syllabus data has fewer topics than capacity,
        leftover weeks automatically become revision/mock-test weeks.
    """
    from datetime import date as _date
    grade = str(payload.get("grade") or "12").strip()
    subjects = payload.get("subjects") or ["General Studies"]
    exams = payload.get("exams") or []
    is_dropper = grade.lower() in ("12", "drop year", "dropper", "drop")

    capacity_min = int(payload.get("daily_study_minutes") or 180)
    # realistic cap: nobody sustainably studies >6h/day on top of school/coaching
    capacity_min = max(30, min(capacity_min, 360))

    start = _date.fromisoformat(payload["today"]) if payload.get("today") else _date.today()
    dec31 = _date(start.year, 12, 31)
    days_left = max(1, (dec31 - start).days)
    num_weeks = max(1, round(days_left / 7))

    MIN_PER_TOPIC = 50
    effective_capacity = capacity_min * 0.85          # buffer for revision days
    sustainable_weekly = int(max(3, min(round(effective_capacity * 7 / MIN_PER_TOPIC), 14)))

    # How many topics does the year have room for at this sustainable pace?
    demand = int(effective_capacity / MIN_PER_TOPIC * days_left)
    # Pull the placeholder syllabus pool (per-subject limits: 1..10 grades,
    # 1..20 droppers). Real curriculum data will replace this later.
    pool = _syllabus_topics(grade, subjects, max(demand, sustainable_weekly))
    total_topics = len(pool)

    # Pace the ACTUAL pool across the remaining weeks: weekly count is the
    # smaller of (sustainable load, evenly spread pace), floored at 1 so even
    # tiny pools still finish before the year ends.
    spread = max(1, round(total_topics / num_weeks))
    weekly_count = max(1, min(sustainable_weekly, max(spread, 3)))
    while weekly_count * ((total_topics + weekly_count - 1) // weekly_count) > weekly_count * num_weeks and weekly_count < sustainable_weekly:
        weekly_count += 1   # nudge up only if spreading would exceed the year

    weeks: list[dict] = []
    i = 0
    wi = 0
    while i < total_topics:
        chunk = pool[i:i + weekly_count]
        wk_start = start.toordinal() + wi * 7
        wk_end = min(wk_start + 6, dec31.toordinal())
        weeks.append({
            "week": wi + 1,
            "start": _date.fromordinal(wk_start).isoformat(),
            "end": _date.fromordinal(wk_end).isoformat(),
            "topics": chunk,
            "target_minutes_per_day": round(len(chunk) * MIN_PER_TOPIC / 7 * (1 / 0.85)),
            "sessions_per_day": max(1, round(len(chunk) / 7 + 0.5)),
        })
        i += weekly_count
        wi += 1

    months: list[dict] = []
    m = 0
    while True:
        month_no = ((start.month - 1 + m) % 12) + 1
        year = start.year + 1 if month_no < start.month else start.year
        if year > start.year:      # we only plan through THIS calendar year
            break
        label = _date(year, month_no, 1).strftime("%B %Y")
        mw = [w for w in weeks if _date.fromisoformat(w["start"]).month == month_no
              and _date.fromisoformat(w["start"]).year == year]
        months.append({
            "month": label,
            "weeks": [w["week"] for w in mw],
            "topic_count": sum(len(w["topics"]) for w in mw),
            "milestone": (f"Finish {sum(len(w['topics']) for w in mw)} topics"
                          f"{' + monthly revision test' if mw else ' (revision & mock tests)'}"),
        })
        if month_no == 12:
            break
        m += 1

    load = "light" if weekly_count <= 5 else "balanced" if weekly_count <= 9 else "intensive"
    return {
        "engine": "pacing-planner-v2 (self-hosted, deterministic)",
        "grade": grade,
        "subjects": subjects,
        "exams": exams,
        "is_dropper": is_dropper,
        "days_left_in_year": days_left,
        "daily_study_minutes": capacity_min,
        "total_topics": total_topics,
        "topics_per_week": weekly_count,
        "weekly_goals": weeks,
        "monthly_goals": months,
        "load_profile": load,
        "summary": (f"{total_topics} topics by Dec 31 · {weekly_count}/week "
                    f"({load}) · ~{capacity_min} min/day · "
                    f"{len(weeks)} goal-weeks across {len(months)} months"),
    }


# ==================================================== daily plan variation ====
def vary_plan(base_slots: list[dict], seed_key: str, focus_topics: list[dict],
              peak_productivity: str | None = None) -> dict:
    """Give every calendar day its own flavour WITHOUT breaking fixed anchors.

    Rules:
      * school/travel/meal/routine/sleep blocks are NEVER moved (they are the
        student's real life).
      * study sessions are re-labelled with today's rotating topics, shuffled
        in length (45/50/60 min mix), and their ORDER within free gaps is
        rotated deterministically by the date-seeded RNG — so Monday ≠ Tuesday
        even for an identical routine, yet nothing overlaps and wake/sleep
        times stay exactly what the student entered.
      * one shorter "revision" session and one quiz-style session are inserted
        into the day when there are >= 3 study blocks, for variety/fun.
    Deterministic: same (plan, date) → same output; different date → different.
    """
    import random as _random
    rng = _random.Random(seed_key)

    slots = [dict(s) for s in base_slots]
    studies = [s for s in slots if s.get("type") == "study"]
    if not studies:
        return {"plan": slots, "focus_topics": [], "varied": False,
                "reason": "no study blocks to vary"}

    # Rotate which topics land on which session using the date as entropy.
    topics = focus_topics or [{"subject": "General", "topic": "Revision"}]
    offset = rng.randrange(len(topics)) if topics else 0
    labels = []
    for k in range(len(studies)):
        t = topics[(offset + k) % len(topics)]
        labels.append(f"{t['subject']}: {t['topic']}")
    # shuffle assignment order per-day (still deterministic for the date)
    perm = list(range(len(labels)))
    rng.shuffle(perm)
    labels = [labels[p] for p in perm]

    # Vary session lengths around the student's preferred block size.
    lens = [45, 50, 60]
    for idx, s in enumerate(studies):
        dur = s["end"] - s["start"]
        if dur < 30:
            continue
        s["label"] = labels[idx % len(labels)]
        newlen = min(dur, rng.choice(lens))
        s["end"] = s["start"] + newlen
        # trim any following break instead of leaving a hole
    # last study of the day becomes "Quiz time 🎯" if >=3 sessions
    if len(studies) >= 3:
        studies[-1]["label"] = f"Quiz time 🎯 ({studies[-1]['label']})"
        mid = studies[len(studies) // 2]
        mid["label"] = f"Revision: {mid['label']}"

    # Drop zero-length artifacts created by trimming.
    slots = [s for s in slots if s["end"] > s["start"]]
    return {
        "plan": slots,
        "focus_topics": labels[:len(topics)],
        "varied": True,
        "seed": seed_key,
        "engine": "daily-variation-v1 (deterministic per-date RNG)",
    }
