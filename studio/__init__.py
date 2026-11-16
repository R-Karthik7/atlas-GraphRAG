"""GraphRAG Atlas: the application layer (storage, ingestion, retrieval, insight, ops).

The original Neo4j pipeline in ``src/`` stays importable; modules here reuse its
text helpers and can mirror the finished knowledge graph into Neo4j.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

__version__ = "2.0.0"
