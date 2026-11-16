"""Smoke test: confirm the Groq key and model work.  python scripts/check_groq.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from generator import GroqGenerator  # noqa: E402

generator = GroqGenerator()
print("Model:", generator.model)
print(generator.complete(
    [{"role": "user", "content": "Explain what a knowledge graph is in 2 sentences."}],
    max_tokens=500,
))
