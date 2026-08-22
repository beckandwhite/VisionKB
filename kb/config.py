"""
Synchronized configuration for the screenshot knowledgebase pipeline.
All paths, model names, and batch params are defined here so that every
script speaks the same language.

Usage:
    from config import SCREENSHOT_ROOT, ANNOTATIONS_FILE, DB_PATH
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Root locations
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # project root
SCREENSHOT_ROOT = BASE_DIR / "screenshots"          # ~~/Mobile Documents/.../Screenshots
DATA_DIR = BASE_DIR / "data"                       # legacy raw assets location
ANNOTATIONS_DIR = DATA_DIR                         # legacy annotations location
DB_PATH = BASE_DIR / ".workspace" / "DEV" / "wiki.db"  # legacy default; runtime uses config_loader
NGRAM_PATH = ANNOTATIONS_DIR / "note_ngrams.json"  # precomputed notes -> n-gram map

# ---------------------------------------------------------------------------
# Ollama (local) configuration
# ---------------------------------------------------------------------------
OLLAMA_API = os.getenv("OLLAMA_HOST", "http://localhost:11434")
VISION_MODEL = "muse-glimmer:30b"           # or muse-vl:7b, moondream:latest
EMBED_MODEL = "nomic-embed-text"            # nomic-embed-text:latest

# ---------------------------------------------------------------------------
# Classifier defaults
# ---------------------------------------------------------------------------
SUPPORTED_EXT = {".png", ".heic", ".jpg", ".jpeg"}
BATCH_SIZE = 20                              # API calls per batch
CLUSTER_KWARGS = dict(algorithm="hierarchical", min_cluster_size=3)
