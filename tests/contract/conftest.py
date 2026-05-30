"""Add portal-backend service directory to sys.path for contract tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "portal-backend"))
