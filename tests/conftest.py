import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root / "collector"), str(root.parent / "demo-three-tier" / "api")]
