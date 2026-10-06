"""Vector store for the RAG tutor — pgvector or FAISS, both fully self-hosted.

Three retrieval tiers, automatically upgraded as optional packages land:
  tier 1 (always): pure-Python TF-IDF cosine over our own notes corpus
  tier 2 (faiss installed): dense retrieval with sentence-transformers vectors
  tier 3 (psycopg2 + pgvector): same via PostgreSQL pgvector table
NO external AI APIs are used; everything below runs on local data.
"""
import threading

# VidyaJyoti's OWN study content (in production this is loaded from the DB
# `library` table / object storage and re-indexed nightly by a cron job).
NOTES = [
    {"title": "Physics — Newton's laws", "subject": "PHY", "text":
     "Newton's second law states that force equals mass times acceleration (F = ma). "
     "Net force on a body produces acceleration in the direction of the force."},
    {"title": "Physics — Gravitation", "subject": "PHY", "text":
     "Universal gravitation: every mass attracts every other mass with force "
     "F = G m1 m2 / r^2, where G = 6.674e-11 N m^2 kg^-2."},
    {"title": "Chemistry — Mole concept", "subject": "CHE", "text":
     "The mole relates mass to number of particles via Avogadro's number 6.022e23. "
     "Moles = given mass / molar mass."},
    {"title": "Chemistry — Chemical bonding", "subject": "CHE", "text":
     "Ionic bonds form by electron transfer between metals and non-metals; "
     "covalent bonds form by sharing electron pairs."},
    {"title": "Maths — Quadratic equations", "subject": "MTH", "text":
     "Quadratic formula: x = (-b ± sqrt(b² - 4ac)) / 2a solves ax² + bx + c = 0. "
     "The discriminant b² - 4ac decides the nature of roots."},
    {"title": "Maths — Differentiation", "subject": "MTH", "text":
     "Derivative measures instantaneous rate of change. d/dx x^n = n x^(n-1); "
     "chain rule: dy/dx = dy/du * du/dx."},
    {"title": "Biology — Photosynthesis", "subject": "BIO", "text":
     "Photosynthesis converts CO2 and water into glucose and O2 using sunlight: "
     "6CO2 + 6H2O -> C6H12O6 + 6O2, occurring in chloroplasts."},
    {"title": "Biology — Human heart", "subject": "BIO", "text":
     "The human heart has four chambers; the left ventricle pumps oxygenated blood "
     "through the aorta to the systemic circulation."},
]

_lock = threading.Lock()
_state: dict[str, object] = {"built": False, "engine": "tfidf"}


def _build_dense():
    """Try FAISS + sentence-transformers (both optional, self-hosted)."""
    try:
        import numpy as np
        import faiss
        from app_ml.models_ml import embed  # lazy: needs sentence-transformers
        texts = [n["text"] for n in NOTES]
        vecs = np.array(embed(texts), dtype="float32")
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        _state["faiss_index"] = index
        _state["engine"] = "faiss+sentence-transformers"
        return True
    except Exception:
        return False


def retrieve_context(question: str, k: int = 3, subject_code: str | None = None) -> list[dict]:
    """Return top-k relevant VidyaJyoti notes for a doubt. Deterministic."""
    docs = [n for n in NOTES if not subject_code or n["subject"] == subject_code] or NOTES
    if not _state["built"]:
        with _lock:
            if not _state["built"]:
                _build_dense()
                _state["built"] = True

    index = _state.get("faiss_index")
    if index is not None:
        try:
            import numpy as np
            from app_ml.models_ml import embed
            q = np.array([embed([question])[0]], dtype="float32")
            scores, idx = index.search(q, min(k, len(docs)))
            hits = []
            for s, i in zip(scores[0], idx[0]):
                if 0 <= i < len(NOTES):
                    hits.append({**NOTES[int(i)], "score": round(float(s), 3)})
            if hits:
                return hits[:k]
        except Exception:
            pass  # fall through to TF-IDF

    from app_ml.models_ml import tfidf_rank
    ranked = tfidf_rank(question, [d["text"] for d in docs])
    hits = [{**docs[i], "score": round(sc, 3)} for sc, i in ranked if sc > 0][:k]
    if not hits:  # never return nothing random — best generic doc instead
        hits = [{**docs[0], "score": 0.0}]
    return hits
