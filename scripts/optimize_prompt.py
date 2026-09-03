"""CLI for the human-reviewed Phase 3 Prompt optimization loop."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prompt_optimization.clusterer import cluster_badcases
from src.prompt_optimization.patch_generator import generate_patch_proposal, review_patch
from src.prompt_optimization.promotion import promote_candidate, reject_candidate, validate_patch
from src.prompt_optimization.root_cause import summarize_root_cause
from src.prompt_optimization.version_manager import create_candidate_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Human-reviewed Prompt optimization")
    sub = parser.add_subparsers(dest="command", required=True)
    cluster = sub.add_parser("cluster")
    cluster.add_argument("experiment_id"); cluster.add_argument("--baseline", default="")
    root = sub.add_parser("root-cause"); root.add_argument("cluster_id")
    propose = sub.add_parser("propose"); propose.add_argument("cluster_id")
    approve = sub.add_parser("approve"); approve.add_argument("patch_id")
    reject = sub.add_parser("reject"); reject.add_argument("patch_id")
    validate = sub.add_parser("validate")
    validate.add_argument("patch_id"); validate.add_argument("baseline_experiment_id")
    promote = sub.add_parser("promote"); promote.add_argument("validation_id")
    args = parser.parse_args()

    if args.command == "cluster":
        output = cluster_badcases(args.experiment_id, args.baseline)
    elif args.command == "root-cause":
        output = summarize_root_cause(args.cluster_id)
    elif args.command == "propose":
        output = generate_patch_proposal(args.cluster_id)
    elif args.command == "approve":
        review_patch(args.patch_id, True)
        output = {"patch_id": args.patch_id, "candidate_version": create_candidate_version(args.patch_id)}
    elif args.command == "validate":
        output = validate_patch(args.patch_id, args.baseline_experiment_id)
    elif args.command == "promote":
        output = {"active_version": promote_candidate(args.validation_id, human_approved=True)}
    else:
        reject_candidate(args.patch_id, human_approved=True)
        output = {"patch_id": args.patch_id, "status": "rejected"}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
