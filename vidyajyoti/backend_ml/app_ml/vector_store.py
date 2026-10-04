"""Vector store for the RAG tutor — pgvector or FAISS, both fully self-hosted."""
import os

SAMPLE_NOTES = [
    ("Newton's second law states that force equals mass times acceleration (F=ma).", "PHY"),
    ("The mole relates mass to number of particles via Avogadro's number 6.022e23.", "CHE"),
    ("Quadratic formula: x = (-b ± √(b²-4ac)) / 2a solves ax²+bx+c=0.", "MTH"),
]


class FaissStore:
    def __init__(self):
        self.index = None
        self.docs = []

    def build(self):
        import numpy as np
        from app_ml.models_ml import embed
        texts = [t for t, _ in SAMPLE_NOTES]
        vecs = np.array(embed(texts), dtype="float32")
        import faiss
        self.index = faiss.IndexFlatIP(vecs.shape[1])
        self.index.add(vecs)
        self.docs = texts

    def search(self, query: str, k: int = 2):
        import numpy as np
        from app_ml.models_ml import embed
        q = np.array([embed([query])], dtype="float32")
        scores, idx = self.index.search(q, min(k, len(self.docs)))
        return [self.docs[i] for i in idx[0] if i >= 0]


def retrieve_context(question: str, subject_code: str | None = None) -> str:
    """Return top-k relevant VidyaJyoti notes for a doubt (FAISS by default)."""
    try:
        store = FaissStore()
        store.build()
        hits = store.search(question)
        return "\n".join(hits)
    except Exception:
        # Graceful fallback: keyword match over local sample notes.
        words = question.lower().split()
        return "\n".join(t for t, _ in SAMPLE_NOTES if any(w in t.lower() for w in words)) or SAMPLE_NOTES[0][0]
