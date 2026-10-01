# SCILDB_data_exlploration

Repository to augment data from SCILDB (the Supreme Court Indian Law Database).

The JSON files at the root (`cases.json`, `votes.json`, `justices.json`,
`portraits.json`, `meta.json`) are the current SCILDB export. The `scripts/`
folder holds tools that propose additions to that data and write them to a
review sheet. Nothing is written back to `cases.json` until a person has
marked each row, so the data stays under human control.

## Setup

```
pip install -r requirements.txt
```

Run the scripts locally. CourtListener and sct.narf.org are not reachable
from every environment, and both deserve polite, rate-limited requests.

## 1. Fill in the `tribes` field

`cases.json` has 679 cases; 246 have an empty `tribes` list, and many of the
rest hold free text that SCILDB split on commas. `suggest_tribes.py` reads
each case caption plus the titles of any attached briefs (which usually spell
the parties out in full) and matches them against a vocabulary of tribal
names. It can also download opinion text from CourtListener and count tribal
names there, skipping names that appear only inside case citations.

```
python scripts/suggest_tribes.py cases.json
python scripts/suggest_tribes.py cases.json --opinions --token YOUR_COURTLISTENER_TOKEN
```

This writes `review/tribes_review.csv`. Open it in a spreadsheet and work the
columns:

| column | meaning |
| --- | --- |
| `current_tribes` | what the data says now |
| `suggested` | names found in the caption, separated by `; ` |
| `band_options` | when a name covers several recognized nations (Sioux, Apache, Pueblo), the choices to pick from |
| `confidence` | `HIGH` named in the caption; `LOW` the word is part of a place or company name; `GENERIC` a tribal party is present but unnamed; `MEDIUM` found only in opinion text |
| `vs_current` | `fills blank`, `agrees`, `adds: ...`, or `current only` |
| `from_opinion` | opinion-text counts, e.g. `Navajo Nation (14); Hopi Tribe (3)` |
| `reviewer_decision` | you fill in: `accept`, `edit`, or `reject` |
| `final_tribes` | with `edit`: the exact list to store, separated by `; ` |

Then write the approved rows back:

```
python scripts/apply_review.py review/tribes_review.csv --cases cases.json --dry-run
python scripts/apply_review.py review/tribes_review.csv --cases cases.json
```

Only rows marked `accept` or `edit` change anything. Check final names
against the Bureau of Indian Affairs list of federally recognized tribes.

A caption names a tribe in only about a fifth of the cases (Lone Wolf v.
Hitchcock says nothing about the Kiowa). The `--opinions` pass exists for the
other four fifths; expect it to take a while and to need a CourtListener API
token.

## 2. Add a link to the NARF Tribal Supreme Court Project

NARF's project (https://sct.narf.org) collects briefs and opinions for Indian
law cases from October Term 2001 onward. Its case index pages are named by
hand (`us_v_cooley.html`, `carcieri.html`, `navajovusfs.html`), so a URL
cannot be guessed from a case name. `narf_links.py` instead reads NARF's own
term index pages (`termindexes/october2001.html` and later), collects every
link into `/caseindexes/`, and matches each SCILDB case from those terms to
the best-named entry.

```
python scripts/narf_links.py cases.json              # fetch term pages, write review/narf_review.csv
python scripts/narf_links.py cases.json --verify     # also HEAD-check each matched URL
python scripts/narf_links.py cases.json --offline    # reuse pages already saved in review/narf_cache/
```

The sheet shows the matched title, docket number, term page and section
(cert granted, denied, pending), a 0 to 1 name-similarity score, a confidence
label, and runner-up candidates. Mark `reviewer_decision` as `accept`,
`edit` (with `final_url`), or `reject`, then:

```
python scripts/narf_links.py cases.json --apply review/narf_review.csv
```

Accepted rows gain a `narfUrl` field in `cases.json`. Cases before October
Term 2001 are left out unless you pass `--all-terms`, because NARF has no
pages for them.

## Files

- `scripts/scildb_io.py` shared loading and saving; writes `cases.json` back
  in the same one-record-per-line layout so diffs stay readable
- `scripts/suggest_tribes.py` tribe suggestions and review sheet
- `scripts/apply_review.py` writes reviewed tribe decisions into `cases.json`
- `scripts/narf_links.py` NARF link matching, review sheet, and apply step
- `review/` review sheets; downloaded NARF pages and opinion text are cached
  here too and ignored by git
