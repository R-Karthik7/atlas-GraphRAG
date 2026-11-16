"""Central configuration: paths, environment variables and model settings."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"

# Shell environment variables take precedence over the .env file.
load_dotenv(PROJECT_ROOT / ".env")

# The embedding model and the Neo4j vector index must agree on dimensions.
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384
VECTOR_INDEX_NAME = "chunk_embedding_index"

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class ConfigurationError(RuntimeError):
    """Raised when required configuration (such as credentials) is missing."""


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
