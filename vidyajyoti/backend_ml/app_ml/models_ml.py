"""Self-hosted ML models for VidyaJyoti. NO external AI APIs are used here.

All models run locally on our own servers using open-source frameworks:
  - scikit-learn  -> topic recommendation (gradient boosting / logistic regression)
  - heuristics+GBR-> schedule adjustment
  - sentence-transformers + FAISS + a small local HF transformer -> RAG tutor
"""
import os
from typing import Any

# Heavy imports are lazy so the service starts even without torch installed.
_MODEL_CACHE: dict[str, Any] = {}


def get_recommender():
    """Small gradient-boosting model over past scores/time-on-task/skips."""
    if "recommender" not in _MODEL_CACHE:
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(n_estimators=50, max_depth=3)
        # In production we load trained weights from disk:
        path = os.path.join(os.path.dirname(__file__), "..", "weights", "recommender.pkl")
        if os.path.exists(path):
            import pickle
            with open(path, "rb") as f:
                model = pickle.load(f)
        _MODEL_CACHE["recommender"] = model
    return _MODEL_CACHE["recommender"]


def embed(texts: list[str]):
    """Local sentence embeddings via sentence-transformers (self-hosted)."""
    if "embedder" not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        _MODEL_CACHE["embedder"] = SentenceTransformer("all-MiniLM-L6-v2")
    return _MODEL_CACHE["embedder"].encode(texts, normalize_embeddings=True)


def generate_answer(context: str, question: str) -> str:
    """Small self-hosted transformer (google/flan-t5-small) generating the final
    answer from retrieved VidyaJyoti content. Runs locally; no API calls out."""
    if "tutor" not in _MODEL_CACHE:
        from transformers import pipeline
        _MODEL_CACHE["tutor"] = pipeline(
            "text2text-generation", model="google/flan-t5-small", device=-1
        )
    prompt = f"Answer using the context.\nContext: {context}\nQuestion: {question}"
    out = _MODEL_CACHE["tutor"](prompt, max_new_tokens=200)
    return out[0]["generated_text"]
