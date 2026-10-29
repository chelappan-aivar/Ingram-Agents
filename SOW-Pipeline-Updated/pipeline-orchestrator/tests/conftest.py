"""Test path setup.

The `orchestrator` package lives inside `deployment/api/` so that `CodeUri: api/` bundles it
into both Lambdas without a build step. Tests add that directory to the path, which is also
exactly how the Lambda runtime sees it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deployment" / "api"))
sys.path.insert(0, str(ROOT / "tests"))
