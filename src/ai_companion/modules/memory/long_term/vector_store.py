import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import List, Optional

from ai_companion.settings import settings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


@dataclass
class Memory:
    """Represents a memory entry in the vector store."""

    text: str
    metadata: dict
    score: Optional[float] = None

    @property
    def id(self) -> Optional[str]:
        return self.metadata.get("id")

    @property
    def timestamp(self) -> Optional[datetime]:
        ts = self.metadata.get("timestamp")
        return datetime.fromisoformat(ts) if ts else None


class VectorStore:
    """A class to handle vector storage operations using Qdrant."""

    REQUIRED_ENV_VARS = ["QDRANT_URL", "QDRANT_API_KEY"]
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    COLLECTION_NAME = "long_term_memory"
    BUSINESS_COLLECTION_NAME = "allen_carr"
    SIMILARITY_THRESHOLD = 0.9  # Threshold for considering memories as similar

    _instance: Optional["VectorStore"] = None
    _initialized: bool = False

    def __new__(cls) -> "VectorStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._validate_env_vars()
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.EMBEDDING_MODEL)
            self.client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
            self._initialized = True

    def _validate_env_vars(self) -> None:
        """Validate that all required environment variables are set."""
        missing_vars = [var for var in self.REQUIRED_ENV_VARS if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def _collection_exists(self, collection_name: str) -> bool:
        """Check if the memory collection exists."""
        collections = self.client.get_collections().collections
        return any(col.name == collection_name for col in collections)

    def _create_collection(self, collection_name: str) -> None:
        """Create a new collection for storing memories."""
        # sample_embedding = self.model.encode("sample text")
        vector_size = self.model.get_sentence_embedding_dimension() # Vector size is defined by used model
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                # size=len(sample_embedding),
                size=vector_size,
                distance=Distance.COSINE,
            ),
        )

    def find_similar_memory(self, text: str, collection_name: str = "long_term_memory") -> Optional[Memory]:
        """Find if a similar memory already exists.

        Args:
            text: The text to search for

        Returns:
            Optional Memory if a similar one is found
        """
        results = self.search_memories(text, k=1, collections_to_search=[collection_name])
        if results and results[0].score >= self.SIMILARITY_THRESHOLD:
            return results[0]
        return None

    def store_memory(self, text: str, metadata: dict, collection_name: str = "long_term_memory") -> None:
        """Store a new memory in the vector store or update if similar exists.

        Args:
            text: The text content of the memory
            metadata: Additional information about the memory (timestamp, type, etc.)
            collection_name: The name of the collection to store the memory in. Defaults to "long_term_memory".
        """
        if not self._collection_exists(collection_name):
            self._create_collection(collection_name)

        # Check if similar memory exists
        similar_memory = self.find_similar_memory(text, collection_name=collection_name)
        if similar_memory and similar_memory.id:
            metadata["id"] = similar_memory.id  # Keep same ID for update

        embedding = self.model.encode(text)
        point = PointStruct(
            id=metadata.get("id", hash(text)),
            vector=embedding.tolist(),
            payload={
                "text": text,
                **metadata,
            },
        )

        self.client.upsert(
            collection_name=collection_name,
            points=[point],
        )

    def search_memories(self, query: str, k: int = 5, collections_to_search: Optional[List[str]] = None) -> List[Memory]:
        """Search for similar memories in the vector store.

        Args:
            query: Text to search for
            k: Number of results to return
            collections_to_search: List of collection names to search. If None, searches all known collections.
        Returns:
            List of Memory objects
        """
        if collections_to_search is None:
            # Por defecto, busca en ambas si no se especifican colecciones
            collections_to_search = [self.COLLECTION_NAME, self.BUSINESS_COLLECTION_NAME]

        query_embedding = self.model.encode(query)
        all_results: List[Memory] = []

        for collection_name in collections_to_search:
            if not self._collection_exists(collection_name):
                # Opcional: print("Collection {collection_name} does not exist. Skipping search.")
                continue

            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_embedding.tolist(),
                limit=k, # Limita los resultados por cada colección
                with_payload=True,
            )

            for hit in results:
                # Añade el nombre de la colección al metadata para saber de dónde viene
                metadata = {k: v for k, v in hit.payload.items() if k != "text"}
                metadata["source_collection"] = collection_name
                all_results.append(
                    Memory(
                        text=hit.payload["text"],
                        metadata=metadata,
                        score=hit.score,
                    )
                )
        
        # Ordena todos los resultados combinados por score y toma los 'k' mejores de TODO el conjunto.
        # Aquí 'k' del parámetro se usa como límite global para la salida final.
        return sorted(all_results, key=lambda x: x.score if x.score is not None else -1, reverse=True)[:k]


        # if not self._collection_exists():
        #     return []

        # query_embedding = self.model.encode(query)
        # results = self.client.search(
        #     collection_name=self.COLLECTION_NAME,
        #     query_vector=query_embedding.tolist(),
        #     limit=k,
        # )

        # return [
        #     Memory(
        #         text=hit.payload["text"],
        #         metadata={k: v for k, v in hit.payload.items() if k != "text"},
        #         score=hit.score,
        #     )
        #     for hit in results
        # ]


@lru_cache
def get_vector_store() -> VectorStore:
    """Get or create the VectorStore singleton instance."""
    return VectorStore()
