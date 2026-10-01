"""
scildb_io.py -- shared helpers for the SCILDB augmentation scripts.

Loads cases from cases.json (the repository format) or from a CSV/JSON export
that uses other column names, and writes cases.json back in the same
one-record-per-line layout so git diffs stay readable.
"""

import csv
import json
import re
from pathlib import Path

# Column aliases: repository name -> names seen in other SCILDB exports.
ALIASES = {
    "id": ["id", "caseId", "Case ID", "case_id"],
    "name": ["name", "caseName", "Case Name", "case_name"],
    "term": ["term", "Term"],
    "usCite": ["usCite", "US Cite", "citation"],
    "tribes": ["tribes", "Tribes Involved", "tribesInvolved"],
    "url": ["url", "URL", "courtlistener_url"],
    "briefs": ["briefs"],
    "narfUrl": ["narfUrl", "NARF URL"],
}


def _first(record: dict, names: list[str], default=None):
    for n in names:
        if n in record and record[n] is not None:
            return record[n]
    return default


def normalize(record: dict) -> dict:
    """Return a copy with the repository's field names filled in."""
    out = dict(record)
    for key, names in ALIASES.items():
        if key not in out or out[key] is None:
            val = _first(record, names)
            if val is not None:
                out[key] = val
    return out


def load_cases(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):  # e.g. {"cases": [...]}
            data = next(v for v in data.values() if isinstance(v, list))
        cases = [normalize(c) for c in data]
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            cases = [normalize(row) for row in csv.DictReader(f)]
    seen, unique = set(), []
    for c in cases:
        cid = c.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        unique.append(c)
    return unique


def save_cases(path: Path, cases: list[dict]) -> None:
    """Write cases.json in the repository's layout: one compact record per line."""
    lines = [json.dumps(c, ensure_ascii=False, separators=(",", ":")) for c in cases]
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(lines) + "\n]\n")


def repair_text(s: str) -> str:
    """Undo common mojibake such as 'Pimaâ€“Maricopa' -> 'Pima–Maricopa'."""
    if not isinstance(s, str) or not re.search(r"[Â-Ãâ]", s):
        return s
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def current_tribes(raw) -> str:
    """The tribes field may be a list, a delimited string, or empty.

    SCILDB split the original free-text value on commas, so a value such as
    'the Oneida Indian Nation of Wisconsin, and the Oneida of the Thames'
    arrives as two list items. Re-joining on ', ' restores the original text.
    """
    if raw is None:
        return ""
    if isinstance(raw, float):  # NaN from pandas
        return ""
    if isinstance(raw, list):
        items = [repair_text(str(x)).strip() for x in raw if str(x).strip()]
        return ", ".join(items)
    s = str(raw).strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            return current_tribes(json.loads(s))
        except json.JSONDecodeError:
            pass
    return repair_text(s)


def split_tribes(value: str) -> list[str]:
    """Reviewer-entered list: 'Cherokee Nation; Osage Nation' -> ['Cherokee Nation', 'Osage Nation']."""
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in re.split(r"\s*;\s*", str(value)) if part.strip()]


def brief_titles(case: dict) -> list[str]:
    """Titles from the briefs field often carry the full, unabbreviated caption."""
    briefs = case.get("briefs") or []
    if isinstance(briefs, str):
        try:
            briefs = json.loads(briefs)
        except json.JSONDecodeError:
            return []
    titles = []
    for b in briefs:
        if isinstance(b, dict) and b.get("title"):
            titles.append(str(b["title"]))
    return titles


def caption_for(case: dict) -> str:
    """Case name plus every brief title, joined so one regex pass covers all."""
    parts = [str(case.get("name") or "")] + brief_titles(case)
    return " || ".join(p for p in parts if p)
