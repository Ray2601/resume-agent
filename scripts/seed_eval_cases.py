"""Seed the fixed 20-case evaluation dataset from real project inputs."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.dataset import seed_dataset_from_project


if __name__ == "__main__":
    ids = seed_dataset_from_project(ROOT)
    print(f"Seeded {len(ids)} cases: {ids[0]} ... {ids[-1]}")
