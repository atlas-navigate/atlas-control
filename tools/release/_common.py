"""Shared helpers for the release tools (run on a build machine, not a device)."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "system"))

DEFAULT_URL_BASE = "https://github.com/atlas-navigate/atlas-control/releases/download"
