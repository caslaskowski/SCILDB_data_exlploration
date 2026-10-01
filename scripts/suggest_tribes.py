"""
suggest_tribes.py  --  RUN LOCALLY, before data goes into /public.

Proposes candidate values for the "tribes" field and writes a review sheet.
It never overwrites your data; a person makes every final call, then
apply_review.py writes the approved values back into cases.json.

Pass 1 (always): reads the case caption ("name") and the titles of any
        briefs attached to the case, which usually spell out the parties in
        full ("Pueblo of Zia v. U S" -> "Zia v. United States, 168 U.S. 198").
Pass 2 (optional): downloads opinion text from CourtListener for cases where
        Pass 1 found nothing and counts tribal names, ignoring names that sit
        inside case citations.

Usage:
    python scripts/suggest_tribes.py cases.json
    python scripts/suggest_tribes.py scildb_final.csv --out review/tribes_review.csv
    python scripts/suggest_tribes.py cases.json --opinions --token YOUR_COURTLISTENER_TOKEN

Output columns (default review/tribes_review.csv):
    current_tribes     what the data says now (comma-split items re-joined)
    suggested          names found in the caption, separated by "; "
    band_options       when a name covers several recognized nations, the
                       choices a reviewer must pick from
    confidence         HIGH / LOW / GENERIC / blank (see notes column)
    vs_current         how the suggestion relates to current_tribes
    from_opinion       Pass 2 counts, e.g. "Navajo Nation (14); Hopi Tribe (3)"
    reviewer_decision  fill in: accept | edit | reject   (blank = leave as is)
    final_tribes       when decision is "edit": the exact list to store,
                       separated by "; "
"""

import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scildb_io import caption_for, current_tribes, load_cases  # noqa: E402

# ---------------------------------------------------------------------------
# Controlled vocabulary.
#   pattern       regex for how a nation appears in a 19th/20th-century caption
#                 (matched against lower-cased text)
#   label         suggested name
#   band_options  None when the label is one recognized nation; otherwise the
#                 distinct nations a reviewer must choose among
# Check final labels against the BIA list of federally recognized tribes.
# ---------------------------------------------------------------------------
VOCAB = [
    # Southeast / Five Tribes
    (r"\bcherokees?\b", "Cherokee",
     "Cherokee Nation; Eastern Band of Cherokee Indians; United Keetoowah Band"),
    (r"\b(muscogee|muskogee|creeks|creek (nation|indians?|tribe|freedmen)|poarch)\b",
     "Muscogee (Creek) Nation", "Muscogee (Creek) Nation; Poarch Band of Creek Indians"),
    (r"\bchoctaws?\b", "Choctaw",
     "Choctaw Nation of Oklahoma; Mississippi Band of Choctaw Indians; Jena Band of Choctaw"),
    (r"\bchick?asaws?\b", "Chickasaw Nation", None),
    (r"\bseminoles?\b", "Seminole",
     "Seminole Nation of Oklahoma; Seminole Tribe of Florida; Miccosukee Tribe"),
    (r"\bfive civilized tribes\b", "Five Tribes (Cherokee, Chickasaw, Choctaw, Muscogee, Seminole)",
     "Cherokee Nation; Chickasaw Nation; Choctaw Nation; Muscogee (Creek) Nation; Seminole Nation"),
    (r"\bmiccosukee\b", "Miccosukee Tribe of Indians of Florida", None),
    (r"\bcatawba\b", "Catawba Indian Nation", None),
    (r"\b(alabama[- ]coushatta|coushatta)\b", "Coushatta", "Alabama-Coushatta Tribe of Texas; Coushatta Tribe of Louisiana"),
    # Plains
    (r"\bosage\b", "Osage Nation", None),
    (r"\b(sioux|lakota|dakota (indians|tribe|nation)|oglala|rosebud|yankton|sisseton|wahpeton|"
     r"standing rock|cheyenne river|crow creek|lower brule|santee|brule|flandreau|spirit lake|devils lake)\b",
     "Sioux (Oceti Sakowin)",
     "Oglala Sioux Tribe; Rosebud Sioux Tribe; Cheyenne River Sioux Tribe; Standing Rock Sioux Tribe; "
     "Yankton Sioux Tribe; Sisseton-Wahpeton Oyate; Crow Creek Sioux Tribe; Lower Brule Sioux Tribe; "
     "Santee Sioux Nation; Flandreau Santee Sioux Tribe; Spirit Lake Tribe; Fort Peck Assiniboine & Sioux"),
    (r"\b(?<!river )cheyennes?\b(?! river)", "Cheyenne",
     "Northern Cheyenne Tribe; Cheyenne and Arapaho Tribes (Oklahoma)"),
    (r"\barapahoe?s?\b", "Arapaho", "Northern Arapaho Tribe; Cheyenne and Arapaho Tribes (Oklahoma)"),
    (r"\bkiowas?\b", "Kiowa Tribe", None),
    (r"\bcomm?anches?\b", "Comanche Nation", None),
    (r"\bponcas?\b", "Ponca", "Ponca Tribe of Nebraska; Ponca Tribe of Indians of Oklahoma"),
    (r"\bomaha (indians?|tribe|nation|reservation)\b", "Omaha Tribe of Nebraska", None),
    (r"\b(winnebago|ho-?chunk)\b", "Ho-Chunk / Winnebago",
     "Ho-Chunk Nation (Wisconsin); Winnebago Tribe of Nebraska"),
    (r"\bpawnees?\b", "Pawnee Nation", None),
    (r"\b(otoe|oto and missouri|missouria)\b", "Otoe-Missouria Tribe", None),
    (r"\bkaw (nation|tribe|indians?)\b|\bkanza\b", "Kaw Nation", None),
    (r"\bkansas indians\b", "Kansas Indians (1866: Shawnee, Wea, and Miami lands; specify)",
     "Shawnee Tribe; Peoria Tribe (Wea); Miami Tribe of Oklahoma"),
    (r"\bwichitas?\b", "Wichita and Affiliated Tribes", None),
    (r"\bcaddos?\b", "Caddo Nation", None),
    (r"\bblackfeet\b", "Blackfeet Nation", None),
    (r"\bcrow (tribe|indians?|nation|reservation)\b", "Crow Tribe", None),
    (r"\bassinn?iboines?\b", "Assiniboine", "Fort Peck Assiniboine & Sioux Tribes; Fort Belknap Indian Community"),
    (r"\bgros ventres?\b", "Fort Belknap Indian Community (Gros Ventre)", None),
    (r"\b(three affiliated|fort berthold|mandan|hidatsa|arikara)\b", "Mandan, Hidatsa & Arikara Nation", None),
    (r"\bchippewa cree\b", "Chippewa Cree Tribe (Rocky Boy's)", None),
    # Northeast / Great Lakes / Ohio Valley
    (r"\boneidas?\b", "Oneida", "Oneida Indian Nation (New York); Oneida Nation (Wisconsin)"),
    (r"\bsenecas?\b", "Seneca", "Seneca Nation of Indians; Tonawanda Band of Seneca; Seneca-Cayuga Nation"),
    (r"\bonondaga\b", "Onondaga Nation", None),
    (r"\bcayugas?\b", "Cayuga", "Cayuga Nation; Seneca-Cayuga Nation"),
    (r"\b(mohawks?|st\.? regis)\b", "Mohawk", "Saint Regis Mohawk Tribe; Mohawk Council of Akwesasne"),
    (r"\btuscaroras?\b", "Tuscarora Nation", None),
    (r"\bnew york indians\b", "New York Indians (Haudenosaunee nations; specify)",
     "Seneca Nation of Indians; Tonawanda Seneca; Oneida; Onondaga; Cayuga; Tuscarora; Saint Regis Mohawk"),
    (r"\bpequots?\b", "Pequot", "Mashantucket Pequot Tribe; Eastern Pequot"),
    (r"\bmohegans?\b", "Mohegan Tribe of Indians of Connecticut", None),
    (r"\bnarragansett\b", "Narragansett Indian Tribe", None),
    (r"\b(wampanoag|mashpee)\b", "Wampanoag", "Mashpee Wampanoag Tribe; Wampanoag Tribe of Gay Head (Aquinnah)"),
    (r"\bpassamaquoddy\b", "Passamaquoddy Tribe", None),
    (r"\bpenobscot\b", "Penobscot Nation", None),
    (r"\b(maliseet|houlton)\b", "Houlton Band of Maliseet Indians", None),
    (r"\babenakis?\b", "Abenaki", "Abenaki (not federally recognized in U.S.; specify band)"),
    (r"\b(chippewas?|ojibw[ae]y?s?|ojibwa|mille lacs?|red lake|leech lake|white earth|lac du flambeau|"
     r"lac courte oreilles|bad river|fond du lac|bois forte|grand portage|bay mills|sault ste\.? marie|"
     r"saginaw|keweenaw|turtle mountain|pembina)\b",
     "Ojibwe (Chippewa)",
     "Minnesota Chippewa Tribe (White Earth, Leech Lake, Mille Lacs, Fond du Lac, Bois Forte, Grand Portage); "
     "Red Lake Band; Lac du Flambeau Band; Lac Courte Oreilles; Bad River; Bay Mills Indian Community; "
     "Sault Ste. Marie Tribe; Saginaw Chippewa; Keweenaw Bay; Turtle Mountain Band"),
    (r"\bottawas?\b|\bodawa\b", "Odawa (Ottawa)",
     "Little Traverse Bay Bands; Grand Traverse Band; Little River Band; Ottawa Tribe of Oklahoma"),
    (r"\bpott?awatt?omi(e|es|s)?\b|\bgun lake\b|\bmatch-?e-?be-?nash-?she-?wish\b", "Potawatomi",
     "Prairie Band Potawatomi Nation; Citizen Potawatomi Nation; Forest County Potawatomi; "
     "Hannahville; Pokagon Band; Match-E-Be-Nash-She-Wish Band (Gun Lake); Nottawaseppi Huron Band"),
    (r"\bmenominee\b", "Menominee Indian Tribe of Wisconsin", None),
    (r"\b(stockbridge|munsee|brotherto[wn]n)\b", "Stockbridge-Munsee / Brothertown",
     "Stockbridge-Munsee Community; Brothertown Indian Nation (not federally recognized)"),
    (r"\bkickapoos?\b", "Kickapoo",
     "Kickapoo Tribe in Kansas; Kickapoo Tribe of Oklahoma; Kickapoo Traditional Tribe of Texas"),
    (r"\bshawnees?\b", "Shawnee", "Shawnee Tribe; Eastern Shawnee Tribe; Absentee Shawnee Tribe"),
    (r"\bdelaware (indians?|tribe|tribal|nation)s?\b|\bdelawares\b|\blenape\b", "Delaware (Lenape)",
     "Delaware Nation (Oklahoma); Delaware Tribe of Indians; Stockbridge-Munsee"),
    (r"\bmiami (indians?|tribe|nation)\b|\bmiamis\b", "Miami", "Miami Tribe of Oklahoma; Miami Nation of Indiana (not federally recognized)"),
    (r"\b(wea|piankesha?w|kaskaskia)\b", "Peoria Tribe (Wea, Piankeshaw, Kaskaskia)", None),
    (r"\bpeoria\b", "Peoria Tribe of Indians of Oklahoma", None),
    (r"\bwyandott?e?s?\b|\bhurons?\b", "Wyandotte Nation", None),
    (r"\bquaw?paws?\b", "Quapaw Nation", None),
    (r"\b(sac and fox|sacs? and foxe?s|sauk|meskwaki|fox indians)\b", "Sac and Fox",
     "Sac & Fox Nation (Oklahoma); Sac & Fox Tribe of the Mississippi in Iowa (Meskwaki); Sac & Fox Nation of Missouri"),
    (r"\biowa (tribe|indians?)\b", "Iowa Tribe", "Iowa Tribe of Kansas and Nebraska; Iowa Tribe of Oklahoma"),
    (r"\bmodoc\b|\bmoadoc\b", "Modoc", "Klamath Tribes (Modoc); Modoc Nation (Oklahoma)"),
    # Southwest
    (r"\bnavajos?\b|\bdin[eé]\b|\bramah\b", "Navajo Nation", None),
    (r"\bhopis?\b", "Hopi Tribe", None),
    (r"\b(apaches?|mescal[ae]ro|jicarilla|san carlos|white mountain|fort apache)\b", "Apache",
     "San Carlos Apache Tribe; White Mountain Apache Tribe; Mescalero Apache Tribe; Jicarilla Apache Nation; "
     "Fort Sill Apache; Tonto Apache; Yavapai-Apache Nation"),
    (r"\b(pueblo|isleta|taos|acoma|laguna|zuni|sandia|santa ana|santa clara|san ildefonso|tesuque|cochiti|"
     r"jemez|picuris|zia|ysleta|san felipe|santo domingo|kewa|nambe|pojoaque|ohkay owingeh|san juan pueblo|"
     r"santa rosa)\b", "Pueblo",
     "Specify the pueblo (e.g., Santa Clara, Isleta, Zia, Laguna, Acoma, Taos, Santa Ana, Ysleta del Sur)"),
    (r"\b(papago|tohono o.?odham)\b", "Tohono O'odham Nation", None),
    (r"\b(salt river|pima|maricopa (indian|tribe))\b", "Salt River Pima-Maricopa / Gila River",
     "Salt River Pima-Maricopa Indian Community; Gila River Indian Community; Ak-Chin Indian Community"),
    (r"\bgila river\b", "Gila River Indian Community", None),
    (r"\bhualapai\b|\bwalapai\b", "Hualapai Tribe", None),
    (r"\byavapai\b|\bfort mcdowell\b", "Yavapai", "Yavapai-Apache Nation; Fort McDowell Yavapai Nation; Yavapai-Prescott"),
    (r"\b(quechan|yuma (indians?|tribe|reservation)|fort yuma)\b", "Quechan (Fort Yuma)", None),
    (r"\bcocopahs?\b", "Cocopah Indian Tribe", None),
    (r"\bchemehuevis?\b", "Chemehuevi Indian Tribe", None),
    (r"\b(fort moja?ve|moja?ve (indians?|tribe))\b", "Fort Mojave Indian Tribe", None),
    (r"\bcolorado river indian\b", "Colorado River Indian Tribes", None),
    (r"\butes?\b|\buintah\b|\bouray\b", "Ute",
     "Ute Indian Tribe (Uintah & Ouray); Southern Ute Indian Tribe; Ute Mountain Ute Tribe"),
    # California / Great Basin
    (r"\bmission indians?\b", "Mission Indians (specify band)",
     "e.g., Cabazon, Morongo, Pala, Rincon, La Jolla, San Pasqual, Pauma, Agua Caliente, Torres-Martinez"),
    (r"\bcabazon\b", "Cabazon Band of Mission Indians", None),
    (r"\bmorongo\b", "Morongo Band of Mission Indians", None),
    (r"\b(cahuilla|agua caliente|torres-?martinez)\b", "Cahuilla", "Agua Caliente Band; Torres-Martinez Desert Cahuilla; Cahuilla Band"),
    (r"\b(pala|rincon|la jolla|san pasqual|pauma)\b", "Luiseño / Kumeyaay bands",
     "Pala Band; Rincon Band; La Jolla Band; San Pasqual Band; Pauma Band"),
    (r"\byuroks?\b", "Yurok Tribe", None),
    (r"\bkaru?ks?\b", "Karuk Tribe", None),
    (r"\bhoopa\b|\bhupa\b", "Hoopa Valley Tribe", None),
    (r"\btolowa\b", "Tolowa Dee-ni' Nation", None),
    (r"\bpaiutes?\b|\bpyramid lake\b|\breno indian colony\b|\bbishop\b", "Paiute",
     "Pyramid Lake Paiute Tribe; Reno-Sparks Indian Colony; Bishop Paiute Tribe; Paiute Indian Tribe of Utah; Burns Paiute"),
    (r"\bshoshones?\b|\bshoshoni\b|\bwind river\b", "Shoshone",
     "Eastern Shoshone (Wind River); Shoshone-Bannock (Fort Hall); Northwestern Band of Shoshone; Te-Moak Western Shoshone; Fallon Paiute-Shoshone"),
    (r"\bbannocks?\b", "Shoshone-Bannock Tribes", None),
    (r"\bwashoe\b", "Washoe Tribe", None),
    # Northwest / Plateau
    (r"\byak[ia]ma\b", "Yakama Nation", None),
    (r"\bnez perc[eé]\b", "Nez Perce Tribe", None),
    (r"\bcolville\b", "Confederated Tribes of the Colville Reservation", None),
    (r"\bspokane (tribe|indians?|reservation)\b", "Spokane Tribe", None),
    (r"\b(coeur d.?alene)\b", "Coeur d'Alene Tribe", None),
    (r"\b(salish|kootenai|flathead)\b", "Confederated Salish and Kootenai Tribes", None),
    (r"\b(umatilla|cayuse|walla walla (indians?|tribe))\b", "Confederated Tribes of the Umatilla Indian Reservation", None),
    (r"\bwarm springs\b", "Confederated Tribes of Warm Springs", None),
    (r"\bklamath (tribe|tribes|indians?|reservation|and)\b", "Klamath Tribes", None),
    (r"\b(tillamooks?|alcea|siletz)\b", "Confederated Tribes of Siletz Indians (Alcea/Tillamook)", None),
    (r"\bpuyallup\b", "Puyallup Tribe", None),
    (r"\bnisqually\b", "Nisqually Indian Tribe", None),
    (r"\bsuquamish\b", "Suquamish Tribe", None),
    (r"\btulalip\b", "Tulalip Tribes", None),
    (r"\blummi\b", "Lummi Nation", None),
    (r"\bmakah\b", "Makah Tribe", None),
    (r"\bquina[iu]l[ae]?t\b|\bquinault\b", "Quinault Indian Nation", None),
    (r"\bquil+e[hy]?ute\b", "Quileute Tribe", None),
    (r"\bupper skagit\b", "Upper Skagit Indian Tribe", None),
    (r"\bchehalis\b", "Confederated Tribes of the Chehalis Reservation", None),
    (r"\bcowlitz\b", "Cowlitz Indian Tribe", None),
    (r"\bchinook\b", "Chinook Indian Nation (not federally recognized)", None),
    (r"\bskokomish\b", "Skokomish Indian Tribe", None),
    # Alaska
    (r"\bvenetie\b", "Native Village of Venetie", None),
    (r"\bkake\b", "Organized Village of Kake", None),
    (r"\bangoon\b", "Angoon Community Association", None),
    (r"\bmetlakatla\b", "Metlakatla Indian Community", None),
    (r"\btee-?hit-?ton\b|\btlingit\b", "Tlingit (Tee-Hit-Ton)", None),
    (r"\bgambell\b", "Native Village of Gambell", None),
    (r"\bnoatak\b", "Native Village of Noatak", None),
    (r"\bstebbins\b", "Village of Stebbins", None),
    (r"\bkarluk\b", "Native Village of Karluk", None),
    (r"\baleut\b", "Aleut", "Aleut Community of St. Paul Island; specify"),
    (r"\b(alaska native|native village|village of|native corporation)\b", "Alaska Native entity (specify)", None),
]
COMPILED = [(re.compile(p), label, opts) for p, label, opts in VOCAB]

# Phrases where a tribal word is really a place, company, or person. These
# are blanked out before matching; a name that survives is HIGH confidence,
# one that only appeared inside a blanked phrase is reported as LOW.
FALSE_FRIENDS = [
    r"\b[\w'.-]+ county\b", r"\bcounty of [\w'.-]+\b",
    r"\b[\w'.-]+ city\b", r"\bcity of [\w'.-]+\b",
    r"\bsioux falls\b", r"\bcrow dog\b", r"\bblackfeather\b",
    r"\b(north|south) dakota\b", r"\bdakota territory\b",
    r"\bchoctaw, o(klahoma|\.)?\s*(&|and)\s*g", r"\bmissouri, kansas (&|and) texas\b",
    r"\bcherokee (strip|outlet) live stock\b",
    r"\bcreek county\b", r"\bosage oil\b", r"\bdelaware (river|bay|state)\b",
    r"\bmiami (investment|beach|herald)\b", r"\bbishop\b(?= community| colony)",
]
FALSE_FRIENDS_RE = re.compile("|".join(FALSE_FRIENDS))

# Words that say "a tribal party is here" without naming it.
GENERIC_RE = re.compile(
    r"\b(indians?|tribes?|tribal|nation|bands?|pueblo|rancheria|colony|community|reservation|"
    r"native village|village of|freedmen|allottees?|chief|principal chief)\b"
)

# Rough pattern for a case citation like "Cherokee Nation v. Georgia, 30 U.S. 1".
# Used in Pass 2 so a tribe named only in a citation is not counted.
CITATION = re.compile(r"[A-Z][\w.'&,\- ]{0,80}? v\. [A-Z][\w.'&\- ]{0,60}?(?:,\s*\d+\s+[A-Z.\s]+\d+)?")


def find_tribes(text: str) -> tuple[list[str], list[str]]:
    """Return (labels found after false friends are blanked, labels lost to blanking)."""
    low = text.lower()
    cleaned = FALSE_FRIENDS_RE.sub(" ", low)
    strong, weak = [], []
    for rx, label, _ in COMPILED:
        if rx.search(cleaned):
            if label not in strong:
                strong.append(label)
        elif rx.search(low) and label not in weak:
            weak.append(label)
    return strong, weak


def band_options(labels: list[str]) -> str:
    lookup = {label: opts for (_, label, opts) in VOCAB}
    return " | ".join(f"{l}: {lookup[l]}" for l in labels if lookup.get(l))


def compare(current: str, labels: list[str]) -> str:
    """How the suggestion relates to what the data already says."""
    if not current and not labels:
        return ""
    if not current:
        return "fills blank"
    if not labels:
        return "current only (caption names no tribe)"
    low = current.lower()
    lookup = {label: rx for (rx, label, _) in COMPILED}
    missing = [l for l in labels if not lookup[l].search(low)]
    return "agrees" if not missing else "adds: " + "; ".join(missing)


# ---------------------------------------------------------------------------
# Pass 2: CourtListener opinion text
# ---------------------------------------------------------------------------
CL_API = "https://www.courtlistener.com/api/rest/v4"


def cluster_id(url: str) -> str:
    m = re.search(r"/opinion/(\d+)/", str(url or ""))
    return m.group(1) if m else ""


def opinion_text(cid: str, token: str, session) -> str:
    """Fetch every sub-opinion of a cluster and return their plain text, tags stripped."""
    headers = {"Authorization": f"Token {token}"} if token else {}
    resp = session.get(f"{CL_API}/clusters/{cid}/", headers=headers, timeout=30)
    resp.raise_for_status()
    texts = []
    for op_url in resp.json().get("sub_opinions", [])[:4]:
        r = session.get(op_url, headers=headers, timeout=30)
        r.raise_for_status()
        op = r.json()
        for field in ("plain_text", "html_with_citations", "html_lawbox", "html_columbia",
                      "xml_harvard", "html_anon_2020", "html"):
            if op.get(field):
                texts.append(html.unescape(re.sub(r"<[^>]+>", " ", op[field])))
                break
        time.sleep(1)  # be polite to CourtListener
    return "\n".join(texts)


def score_opinion(text: str) -> str:
    """Return 'Name (count)' pairs, citations removed, most-mentioned first."""
    cleaned = FALSE_FRIENDS_RE.sub(" ", CITATION.sub(" ", text).lower())
    counts = Counter()
    for rx, label, _ in COMPILED:
        n = len(rx.findall(cleaned))
        if n:
            counts[label] += n
    return "; ".join(f"{name} ({n})" for name, n in counts.most_common(5))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="cases.json or a CSV/JSON export")
    ap.add_argument("--out", type=Path, default=Path("review/tribes_review.csv"))
    ap.add_argument("--opinions", action="store_true",
                    help="Pass 2: scan CourtListener opinion text when the caption names no tribe")
    ap.add_argument("--opinions-all", action="store_true",
                    help="Pass 2 for every case, even when the caption already names a tribe")
    ap.add_argument("--token", default="", help="CourtListener API token (recommended for Pass 2)")
    ap.add_argument("--cache", type=Path, default=Path("review/opinion_cache.json"),
                    help="where Pass 2 keeps downloaded opinion text between runs")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N cases (testing)")
    args = ap.parse_args()

    cases = load_cases(args.input)
    if args.limit:
        cases = cases[: args.limit]

    cache = {}
    session = None
    if args.opinions or args.opinions_all:
        import requests  # imported here so Pass 1 works without it
        session = requests.Session()
        if args.cache.exists():
            cache = json.loads(args.cache.read_text(encoding="utf-8"))

    rows = []
    for case in cases:
        caption = caption_for(case)
        strong, weak = find_tribes(caption)
        current = current_tribes(case.get("tribes"))
        notes = []

        if strong:
            confidence = "HIGH: named in caption"
        elif weak:
            confidence = "LOW: tribal word is part of a place, company, or person name"
        elif GENERIC_RE.search(caption.lower()):
            confidence = "GENERIC: caption mentions a tribal party but no name matched"
        else:
            confidence = ""
        if weak and strong:
            notes.append("also appears only as place/company name: " + "; ".join(weak))

        row = {
            "id": case.get("id", ""),
            "name": case.get("name", ""),
            "term": case.get("term", ""),
            "usCite": case.get("usCite", ""),
            "current_tribes": current,
            "suggested": "; ".join(strong or weak),
            "band_options": band_options(strong or weak),
            "confidence": confidence,
            "vs_current": compare(current, strong),
            "from_opinion": "",
            "notes": "",
            "reviewer_decision": "",
            "final_tribes": "",
            "url": case.get("url", ""),
        }

        want_pass2 = args.opinions_all or (args.opinions and not strong and not current)
        if want_pass2:
            cid = cluster_id(case.get("url"))
            if not cid:
                notes.append("no CourtListener cluster id in url")
            else:
                try:
                    if cid not in cache:
                        cache[cid] = opinion_text(cid, args.token, session)
                        args.cache.parent.mkdir(parents=True, exist_ok=True)
                        args.cache.write_text(json.dumps(cache), encoding="utf-8")
                    row["from_opinion"] = score_opinion(cache[cid])
                    if row["from_opinion"] and not strong:
                        row["confidence"] = "MEDIUM: mentioned in opinion text; verify"
                except Exception as err:  # keep going if one request fails
                    notes.append(f"fetch failed: {err}")

        row["notes"] = " | ".join(notes)
        rows.append(row)

    import pandas as pd
    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")

    conf = out["confidence"].astype(str)
    print(f"Cases reviewed:                 {len(out)}")
    print(f"tribes blank now:               {(out['current_tribes'] == '').sum()}")
    print(f"Caption suggestions:            {(out['suggested'] != '').sum()}")
    print(f"  HIGH confidence:              {conf.str.startswith('HIGH').sum()}")
    print(f"  LOW confidence:               {conf.str.startswith('LOW').sum()}")
    print(f"  fills a blank:                {(out['vs_current'] == 'fills blank').sum()}")
    print(f"  adds to current value:        {out['vs_current'].str.startswith('adds').sum()}")
    print(f"Generic tribal party, no name:  {conf.str.startswith('GENERIC').sum()}")
    print(f"Opinion-text suggestions:       {(out['from_opinion'] != '').sum()}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
