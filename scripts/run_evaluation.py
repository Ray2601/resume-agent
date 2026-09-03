"""Run or compare fixed-dataset Prompt experiments from the command line."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation import compare_experiments, run_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt evaluation Harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--dataset", default="dataset_v1.0")
    run.add_argument("--writer", required=True, choices=["writer_v1.0", "writer_v1.1"])
    run.add_argument("--hr", default="hr_v1.0", choices=["hr_v1.0"])
    run.add_argument("--model", default="")
    run.add_argument("--name", default="")
    compare = sub.add_parser("compare")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    args = parser.parse_args()

    if args.command == "run":
        print(run_experiment(args.dataset, args.writer, args.hr, args.model, args.name))
    else:
        print(json.dumps(compare_experiments(args.baseline, args.candidate),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
