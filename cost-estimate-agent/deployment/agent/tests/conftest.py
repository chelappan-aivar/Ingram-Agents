import sys
from pathlib import Path

# main.py and cost_estimate/ live in agent/ (the container's /opt/app); make them importable from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
