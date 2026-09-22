"""
Vector store module for RAG-based tutor.

Supports both FAISS (in-memory/file-based) and pgvector (PostgreSQL-based) backends.
All embeddings are generated locally using sentence-transformers.
"""

import os
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


class VectorStore:
    """
    Abstract base class for vector storage backends.
    """
    
    def add(self, embeddings: np.ndarray, metadata: List[Dict[str, Any]]) -> None:
        raise NotImplementedError
    
    def search(
        self, 
        query_embedding: np.ndarray, 
        top_k: int = 5
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        raise NotImplementedError
    
    def save(self, path: str) -> None:
        raise NotImplementedError
    
    def load(self, path: str) -> None:
        raise NotImplementedError


class FAISSVectorStore(VectorStore):
    """
    FAISS-based vector store for in-memory similarity search.
    
    Suitable for moderate-sized datasets that fit in RAM.
    For larger datasets, use pgvector or distributed FAISS.
    """
    
    def __init__(self, dimension: int = 384):
        """
        Initialize FAISS index.
        
        Args:
            dimension: Embedding dimension (384 for all-MiniLM-L6-v2)
        """
        self.dimension = dimension
        self.index = None
        self.metadata_store: List[Dict[str, Any]] = []
        self.is_initialized = False
    
    def initialize(self) -> None:
        """Initialize FAISS index."""
        try:
            import faiss
            # Use L2 distance (Euclidean) for simplicity
            # Can also use IP (Inner Product) for cosine similarity with normalized vectors
            self.index = faiss.IndexFlatL2(self.dimension)
            self.is_initialized = True
        except ImportError:
            raise ImportError("FAISS not installed. Run: pip install faiss-cpu")
    
    def add(self, embeddings: np.ndarray, metadata: List[Dict[str, Any]]) -> None:
        """Add embeddings to the index."""
        if not self.is_initialized:
            self.initialize()
        
        assert len(embeddings) == len(metadata), "Embeddings and metadata must have same length"
        assert embeddings.shape[1] == self.dimension, f"Expected {self.dimension} dimensions"
        
        self.index.add(embeddings.astype(np.float32))
        self.metadata_store.extend(metadata)
    
    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """
        Search for most similar vectors.
        
        Returns:
            distances: Array of distances to nearest neighbors
            results: List of metadata for nearest neighbors
        """
        if not self.is_initialized:
            self.initialize()
        
        distances, indices = self.index.search(
            query_embedding.reshape(1, -1).astype(np.float32),
            top_k
        )
        
        results = [self.metadata_store[i] for i in indices[0] if i < len(self.metadata_store)]
        return distances[0], results
    
    def save(self, path: str) -> None:
        """Save FAISS index to disk."""
        import faiss
        faiss.write_index(self.index, path)
        # Also save metadata separately
        import json
        with open(f"{path}.meta.json", 'w') as f:
            json.dump(self.metadata_store, f)
    
    def load(self, path: str) -> None:
        """Load FAISS index from disk."""
        import faiss
        import json
        
        self.index = faiss.read_index(path)
        with open(f"{path}.meta.json", 'r') as f:
            self.metadata_store = json.load(f)
        self.is_initialized = True


class PGVectorStore(VectorStore):
    """
    PostgreSQL + pgvector based vector store.
    
    Suitable for large-scale deployments with persistent storage.
    Requires pgvector extension installed in PostgreSQL.
    """
    
    def __init__(self, connection_string: str, table_name: str = "content_embeddings"):
        """
        Initialize pgvector store.
        
        Args:
            connection_string: PostgreSQL connection URL
            table_name: Table name for storing embeddings
        """
        self.connection_string = connection_string
        self.table_name = table_name
        self.dimension = 384
        self.is_initialized = False
    
    async def initialize(self) -> None:
        """Create table and indexes if they don't exist."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        
        engine = create_async_engine(self.connection_string)
        
        async with engine.begin() as conn:
            # Enable pgvector extension
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            
            # Create embeddings table
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id SERIAL PRIMARY KEY,
                    content_text TEXT NOT NULL,
                    embedding vector({self.dimension}),
                    subject_id INTEGER,
                    topic_id INTEGER,
                    source_title TEXT,
                    source_page INTEGER,
                    metadata JSONB
                )
            """))
            
            # Create HNSW index for fast approximate nearest neighbor search
            await conn.execute(text(f"""
                CREATE INDEX IF NOT EXISTS embedding_idx 
                ON {self.table_name} 
                USING hnsw (embedding vector_l2_ops)
            """))
        
        await engine.dispose()
        self.is_initialized = True
    
    async def add(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict[str, Any]]
    ) -> None:
        """Add embeddings to PostgreSQL."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        
        engine = create_async_engine(self.connection_string)
        
        async with engine.begin() as conn:
            for emb, meta in zip(embeddings, metadata):
                embedding_str = "[" + ",".join(map(str, emb.tolist())) + "]"
                await conn.execute(text(f"""
                    INSERT INTO {self.table_name} 
                    (content_text, embedding, subject_id, topic_id, source_title, source_page, metadata)
                    VALUES (:content, :embedding::vector, :subject_id, :topic_id, :title, :page, :metadata)
                """), {
                    "content": meta.get("content_text", ""),
                    "embedding": embedding_str,
                    "subject_id": meta.get("subject_id"),
                    "topic_id": meta.get("topic_id"),
                    "title": meta.get("source_title"),
                    "page": meta.get("source_page"),
                    "metadata": meta.get("extra", {})
                })
        
        await engine.dispose()
    
    async def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Search for most similar vectors using pgvector."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        
        engine = create_async_engine(self.connection_string)
        embedding_str = "[" + ",".join(map(str, query_embedding.tolist())) + "]"
        
        async with engine.begin() as conn:
            result = await conn.execute(text(f"""
                SELECT id, content_text, subject_id, topic_id, source_title, source_page, metadata,
                       embedding <-> :query_embedding AS distance
                FROM {self.table_name}
                ORDER BY embedding <-> :query_embedding
                LIMIT :top_k
            """), {"query_embedding": embedding_str, "top_k": top_k})
            
            rows = result.fetchall()
            
            distances = np.array([row.distance for row in rows])
            results = [{
                "id": row.id,
                "content_text": row.content_text,
                "subject_id": row.subject_id,
                "topic_id": row.topic_id,
                "source_title": row.source_title,
                "source_page": row.source_page,
                "metadata": row.metadata
            } for row in rows]
        
        await engine.dispose()
        return distances, results
    
    def save(self, path: str) -> None:
        """Not needed - data is persisted in PostgreSQL."""
        pass
    
    def load(self, path: str) -> None:
        """Not needed - data is loaded from PostgreSQL."""
        pass
