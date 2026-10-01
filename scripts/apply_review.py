"""
apply_review.py  --  write a reviewed tribes sheet back into cases.json.

Reads review/tribes_review.csv (from suggest_tribes.py) after a person has
filled in reviewer_decision:
    accept   store the "suggested" column as the case's tribes list
    edit     store the "final_tribes" column (names separated by "; ")
    reject   leave the case unchanged
Blank rows are left unchanged. Nothing is written without a decision.

Usage:
    python scripts/apply_review.py review/tribes_review.csv --cases cases.json
    python scripts/apply_review.py review/tribes_review.csv --cases cases.json --dry-run

For NARF URLs use:  python scripts/narf_links.py cases.json --apply review/narf_review.csv
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scildb_io import load_cases, save_cases, split_tribes  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet", type=Path, help="reviewed tribes_review.csv")
    ap.add_argument("--cases", type=Path, default=Path("cases.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    by_id = {c["id"]: c for c in cases}
    changed, skipped = 0, []

    with open(args.sheet, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            decision = (row.get("reviewer_decision") or "").strip().lower()
            if decision not in ("accept", "edit"):
                continue
            case = by_id.get(row.get("id"))
            if not case:
                skipped.append(f"{row.get('id')}: not in {args.cases}")
                continue
            value = row.get("suggested") if decision == "accept" else row.get("final_tribes")
            tribes = split_tribes(value or "")
            if not tribes:
                skipped.append(f"{row.get('id')}: decision '{decision}' but no names given")
                continue
            if case.get("tribes") != tribes:
                print(f"{case['id']}  {case.get('name', '')[:60]}\n    {case.get('tribes')} -> {tribes}")
                case["tribes"] = tribes
                changed += 1

    for s in skipped:
        print("skipped:", s)
    print(f"{'Would update' if args.dry_run else 'Updated'} {changed} case(s)")
    if changed and not args.dry_run:
        save_cases(args.cases, cases)
        print(f"Wrote {args.cases}")


if __name__ == "__main__":
    main()
