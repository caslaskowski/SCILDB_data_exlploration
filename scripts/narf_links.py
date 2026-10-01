"""
narf_links.py  --  RUN LOCALLY. Finds the NARF Tribal Supreme Court Project
case index page for each SCILDB case and writes a review sheet.

NARF's project began with October Term 2001, and its case index pages are
named by hand ("us_v_cooley.html", "carcieri.html", "navajovusfs.html"), so
guessing a URL from a case name is unreliable. Instead this script reads
NARF's own term index pages (https://sct.narf.org/termindexes/october2001.html
and later), collects every link into /caseindexes/, and matches each SCILDB
case from those terms to the best-named entry.

Usage:
    python scripts/narf_links.py cases.json                 # fetch term pages, write review sheet
    python scripts/narf_links.py cases.json --offline       # reuse pages saved in review/narf_cache/
    python scripts/narf_links.py cases.json --verify        # also HEAD-check each matched URL
    python scripts/narf_links.py cases.json --apply review/narf_review.csv   # write narfUrl into cases.json

Review sheet columns (review/narf_review.csv):
    narf_url, narf_title, narf_docket, narf_term_page, narf_section   best match
    score              0-1 name similarity (1.0 = every party token matched)
    confidence         HIGH (>= 0.85) / MEDIUM (>= 0.6) / LOW / NONE
    alternatives       runner-up candidates, "title -> url (score)"
    reviewer_decision  accept | edit | reject   (blank = leave as is)
    final_url          when decision is "edit": the URL to store
"""

import argparse
import csv
import difflib
import html
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scildb_io import load_cases, save_cases  # noqa: E402

NARF_HOST = "https://sct.narf.org"
FIRST_TERM = 2001
TERM_PAGE = NARF_HOST + "/termindexes/october{year}.html"
USER_AGENT = "SCILDB-data-exploration (research; contact repository owner)"

# ---------------------------------------------------------------------------
# Fetching and parsing NARF term index pages
# ---------------------------------------------------------------------------
ANCHOR_OR_HEADING = re.compile(
    r"<h([1-6])[^>]*>(?P<heading>.*?)</h\1>|<a\s[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<text>.*?)</a>",
    re.I | re.S,
)
TAG = re.compile(r"<[^>]+>")
DOCKET = re.compile(r"\b(\d{2}-\d{1,5}[A-Z]?|\d{2}[AaMmOo]\d{1,5})\b")


def clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", fragment))).strip()


def canonical(url: str) -> str:
    """Collapse www./mail. hosts and http so the same page has one URL."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    host = re.sub(r"^(www|mail)\.", "", host)
    return urlunsplit(("https", host, parts.path, "", ""))


def parse_term_page(page_html: str, page_url: str, year: int) -> list[dict]:
    entries, section = [], ""
    for m in ANCHOR_OR_HEADING.finditer(page_html):
        if m.group("heading") is not None:
            section = clean(m.group("heading"))
            continue
        href = m.group("href")
        if "caseindexes/" not in href.lower():
            continue
        text = clean(m.group("text"))
        if not text:
            continue
        docket = DOCKET.search(text)
        entries.append({
            "url": canonical(urljoin(page_url, href)),
            "title": text,
            "docket": docket.group(1) if docket else "",
            "term_page": year,
            "section": section,
        })
    return entries


def fetch_term_pages(years: range, cache_dir: Path, offline: bool, delay: float) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    session = None
    for year in years:
        page_url = TERM_PAGE.format(year=year)
        cached = cache_dir / f"october{year}.html"
        page_html = None
        if cached.exists():
            page_html = cached.read_text(encoding="utf-8", errors="replace")
        elif not offline:
            if session is None:
                import requests
                session = requests.Session()
                session.headers["User-Agent"] = USER_AGENT
            try:
                resp = session.get(page_url, timeout=30)
                if resp.status_code == 200:
                    page_html = resp.text
                    cached.write_text(page_html, encoding="utf-8")
                else:
                    print(f"  {page_url}: HTTP {resp.status_code}", file=sys.stderr)
            except Exception as err:
                print(f"  {page_url}: {err}", file=sys.stderr)
            time.sleep(delay)
        if page_html:
            found = parse_term_page(page_html, page_url, year)
            print(f"  october{year}.html: {len(found)} case links")
            entries.extend(found)
    # One row per URL. A case can sit on several term pages (cert pending one
    # term, granted the next); keep the latest listing, which carries the
    # current status, and remember the other terms for matching.
    by_url = {}
    for e in entries:
        prev = by_url.get(e["url"])
        if prev is None:
            by_url[e["url"]] = e
        else:
            e["also_terms"] = prev.get("also_terms", []) + [prev["term_page"]]
            by_url[e["url"]] = e
    return list(by_url.values())


# ---------------------------------------------------------------------------
# Matching SCILDB case names to NARF entries
# ---------------------------------------------------------------------------
ABBREV = {
    "u.s.": "us", "u s": "us", "united states": "us", "united states of america": "us",
    "department of the interior": "doi", "department of interior": "doi", "dept of interior": "doi",
    "secretary of the interior": "doi", "secretary of interior": "doi",
    "ass'n": "association", "assn": "association", "corp": "corporation", "co": "company",
    "inc": "incorporated", "dept": "department", "dist": "district", "gen": "general",
    "ins": "insurance", "comm'n": "commission", "commn": "commission", "nat'l": "national",
    "natl": "national", "mfg": "manufacturing", "r.r.": "railroad", "ry": "railway",
    "cal": "california", "miss": "mississippi", "n.y.": "new york", "ny": "new york",
    "okl": "oklahoma", "okla": "oklahoma", "wash": "washington", "mich": "michigan",
    "wis": "wisconsin", "minn": "minnesota", "ariz": "arizona", "n.m.": "new mexico",
    "s.d.": "south dakota", "n.d.": "north dakota", "mont": "montana", "neb": "nebraska",
    "fla": "florida", "ga": "georgia", "tex": "texas", "colo": "colorado", "wyo": "wyoming",
    "nev": "nevada", "ore": "oregon", "ida": "idaho", "kan": "kansas", "conn": "connecticut",
}
STOP = {"the", "of", "and", "et", "al", "a", "an", "in", "on", "for", "by", "ex", "rel",
        "state", "states", "indian", "indians", "tribe", "tribes", "nation", "band", "bands",
        "community", "reservation", "inc", "incorporated", "company", "corporation", "llc",
        "limited", "association", "department", "secretary", "county", "city", "town",
        "board", "commissioners", "commission", "director", "office", "its", "official",
        "capacity", "chief", "chairman", "no"}


def normalize_name(name: str) -> str:
    s = html.unescape(str(name or "")).lower()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\(.*?\)", " ", s)             # drop docket numbers and parentheticals
    s = re.sub(r",\s*\d+\s+u\.?s\.?.*$", " ", s)  # drop a trailing citation
    s = re.sub(r"\bvs?\.?\b", " v ", s)         # "vs.", "v." -> " v "
    for long, short in sorted(ABBREV.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(r"(?<![\w.])" + re.escape(long) + r"(?![\w])", short, s)
    s = re.sub(r"[^\w\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sides(name: str) -> list[list[str]]:
    """Split 'a v b' into token lists for each side, stop words removed."""
    norm = normalize_name(name)
    parts = norm.split(" v ", 1) if " v " in norm else [norm]
    out = []
    for p in parts:
        toks = [t for t in re.split(r"[\s-]+", p) if t and t not in STOP]
        out.append(toks or [t for t in re.split(r"[\s-]+", p) if t])
    return out


def token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.86


def side_score(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    hits_a = sum(any(token_match(x, y) for y in b) for x in a)
    hits_b = sum(any(token_match(y, x) for x in a) for y in b)
    return (hits_a / len(a) + hits_b / len(b)) / 2


def name_score(scildb_name: str, narf_title: str) -> float:
    s, n = sides(scildb_name), sides(narf_title)
    if len(s) == 2 and len(n) == 2:
        straight = (side_score(s[0], n[0]) + side_score(s[1], n[1])) / 2
        swapped = (side_score(s[0], n[1]) + side_score(s[1], n[0])) / 2
        return max(straight, 0.9 * swapped)
    flat_s = [t for side in s for t in side]
    flat_n = [t for side in n for t in side]
    return 0.8 * side_score(flat_s, flat_n)


def best_matches(case: dict, entries: list[dict], top: int = 3) -> list[tuple[float, dict]]:
    term = int(case.get("term") or 0)
    scored = []
    for e in entries:
        pages = [e["term_page"]] + e.get("also_terms", [])
        gap = min(abs(term - p) for p in pages)
        if gap > 2:
            continue
        score = name_score(case.get("name", ""), e["title"])
        if gap == 0:
            score += 0.05
        elif gap == 1:
            score += 0.02
        scored.append((round(min(score, 1.0), 3), e))
    scored.sort(key=lambda x: -x[0])
    return scored[:top]


MIN_SCORE = 0.3  # below this a candidate is noise, not a suggestion


def confidence(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


def head_ok(url: str, session) -> str:
    try:
        r = session.head(url, timeout=20, allow_redirects=True)
        if r.status_code == 405:
            r = session.get(url, timeout=20, stream=True)
        return str(r.status_code)
    except Exception as err:
        return f"error: {err}"


# ---------------------------------------------------------------------------
# Applying a reviewed sheet
# ---------------------------------------------------------------------------
def apply_sheet(sheet: Path, cases_path: Path, dry_run: bool) -> None:
    cases = load_cases(cases_path)
    by_id = {c["id"]: c for c in cases}
    changed = 0
    with open(sheet, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            decision = (row.get("reviewer_decision") or "").strip().lower()
            if decision not in ("accept", "edit"):
                continue
            url = (row.get("narf_url") if decision == "accept" else row.get("final_url")) or ""
            url = url.strip()
            case = by_id.get(row.get("id"))
            if not case or not url:
                continue
            if case.get("narfUrl") != url:
                case["narfUrl"] = url
                changed += 1
    print(f"{'Would set' if dry_run else 'Set'} narfUrl on {changed} case(s)")
    if not dry_run and changed:
        save_cases(cases_path, cases)
        print(f"Wrote {cases_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="cases.json")
    ap.add_argument("--out", type=Path, default=Path("review/narf_review.csv"))
    ap.add_argument("--cache-dir", type=Path, default=Path("review/narf_cache"),
                    help="downloaded term index pages are kept here and reused")
    ap.add_argument("--offline", action="store_true", help="never fetch; use cached term pages only")
    ap.add_argument("--last-term", type=int, default=date.today().year,
                    help=f"last October Term to read (default: this year); first is {FIRST_TERM}")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between NARF requests")
    ap.add_argument("--verify", action="store_true", help="HEAD-check each matched URL")
    ap.add_argument("--all-terms", action="store_true",
                    help="list every case, not only those from Oct. Term 2001 onward")
    ap.add_argument("--apply", type=Path, metavar="REVIEWED_CSV",
                    help="write accepted/edited URLs from a reviewed sheet into the input file")
    ap.add_argument("--dry-run", action="store_true", help="with --apply: report but do not write")
    args = ap.parse_args()

    if args.apply:
        apply_sheet(args.apply, args.input, args.dry_run)
        return

    print("Reading NARF term index pages...")
    entries = fetch_term_pages(range(FIRST_TERM, args.last_term + 1), args.cache_dir, args.offline, args.delay)
    print(f"{len(entries)} distinct NARF case index pages found")

    cases = load_cases(args.input)
    if not args.all_terms:
        cases = [c for c in cases if int(c.get("term") or 0) >= FIRST_TERM - 1]

    session = None
    if args.verify:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

    rows = []
    for case in cases:
        matches = [(s, e) for s, e in best_matches(case, entries) if s >= MIN_SCORE]
        best_score, best = (matches[0] if matches else (0.0, {}))
        row = {
            "id": case.get("id", ""),
            "name": case.get("name", ""),
            "term": case.get("term", ""),
            "usCite": case.get("usCite", ""),
            "current_narfUrl": case.get("narfUrl", "") or "",
            "narf_url": best.get("url", ""),
            "narf_title": best.get("title", ""),
            "narf_docket": best.get("docket", ""),
            "narf_term_page": best.get("term_page", ""),
            "narf_section": best.get("section", ""),
            "score": best_score,
            "confidence": confidence(best_score),
            "alternatives": " || ".join(f"{e['title']} -> {e['url']} ({s})" for s, e in matches[1:]),
            "http_status": "",
            "reviewer_decision": "",
            "final_url": "",
        }
        if args.verify and best.get("url"):
            row["http_status"] = head_ok(best["url"], session)
            time.sleep(args.delay)
        rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for r in rows:
        counts[r["confidence"]] = counts.get(r["confidence"], 0) + 1
    print(f"Cases from Oct. Term {FIRST_TERM} on: {len(rows)}")
    for level in ("HIGH", "MEDIUM", "LOW", "NONE"):
        print(f"  {level:<7}{counts.get(level, 0)}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
