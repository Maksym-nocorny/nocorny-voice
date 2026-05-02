"""Shared fixtures and test path setup."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so tests can import top-level modules
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Provide dummy env so config.py / gemini_service.py don't blow up on import
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
