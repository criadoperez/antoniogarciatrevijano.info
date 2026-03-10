"""
Generate website catalog from processed documents.

Scans output/publicos/ for Docling JSON files, reads origin.filename from
each, parses metadata from the filename convention, cross-references with
ipfs/cids.json for IPFS links, and outputs site/src/data/catalog.json.

Usage:
    python build_catalog.py

Run after convert_documents.py and sync_to_ipfs.py — it needs
output/*.json for source filenames and ipfs/cids.json for IPFS CIDs.

Two-pass catalog:
  Pass 1 — articles and historical docs (via Docling output/*.json)
  Pass 2 — photos (scanned directly from ficheros/publicos/fotos/)
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────

OUTPUT_DIR = Path("output/publicos")
FOTOS_DIR = Path("ficheros/publicos/fotos")
CIDS_FILE = Path("ipfs/cids.json")
CATALOG_FILE = Path("site/src/data/catalog.json")
GATEWAY = "https://ipfs.antoniogarciatrevijano.info/ipfs"

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".tif", ".tiff"}

# Known publications extracted from corpus analysis.
# Used to distinguish publication from title in filenames.
# Sorted longest-first at lookup time to avoid partial matches.
KNOWN_PUBLICATIONS = {
    "ABC",
    "ACRATAS",
    "AHORA",
    "AJOBLANCO",
    "ATENEO",
    "AVUI",
    "BLOG AGT",
    "DERECHO Y OPINION",
    "DIARIO 16",
    "DIARIORC",
    "EL CONFIDENCIAL",
    "EL INDEPENDFIENTE",
    "EL INDEPENDIENTE",
    "EL MUNDO",
    "EL PAIS",
    "EL SIGLO",
    "ELCORREO",
    "ELDIARIODELAMARINA.COM",
    "ELINDEPENDIENTE",
    "ESPIA EN EL CONGRESO",
    "EXTREMADURAPROGRESISTA",
    "FILOSOFIA DIGITAL",
    "IGLESIA VIVA",
    "L'ESTEL DE MALLORCA",
    "LA PROVINCIA",
    "LA RAZON",
    "LAOPINIONDTENERIFE",
    "MADRID",
    "REPORTER",
    "TEINTERESA",
    "YA",
    "EL RINCON DE YANKA",
}

# Sorted longest-first for greedy matching
_PUBS_SORTED = sorted(KNOWN_PUBLICATIONS, key=len, reverse=True)


# ── Filename parser ───────────────────────────────────────────────────

def parse_filename(stem: str) -> dict:
    """
    Parse an article filename stem into structured metadata.

    Expected pattern: YYYY.MMDD.PUBLICATION.TITLE_AGT
    Returns dict with: date, date_raw, publication, title, series_number.
    Handles typos (dashes, commas, apostrophes as separators).
    """
    result = {
        "date": None,
        "date_raw": None,
        "publication": None,
        "title": stem,
        "series_number": None,
    }

    # 1. Extract date prefix: YYYY followed by separator and digits.
    # Handles typos: apostrophes (0'318), extra dots (06.21), commas, dashes.
    # Strategy: match year + separator + a run of digits/separators, then
    # strip non-digits to get the raw MMDD.
    date_match = re.match(r"^(\d{4})[.\-,]([\d\s'.\-]+)", stem)
    if not date_match:
        return result

    year = date_match.group(1)
    raw_digits = re.sub(r"[^0-9]", "", date_match.group(2))

    if len(raw_digits) < 4:
        # Not enough digits for MMDD — treat as year-only
        result["date"] = year
        result["date_raw"] = f"{year}.0000"
    else:
        month = raw_digits[:2]
        day = raw_digits[2:4]
        if month == "00":
            result["date"] = year
        elif day == "00":
            result["date"] = f"{year}-{month}"
        else:
            result["date"] = f"{year}-{month}-{day}"
        result["date_raw"] = f"{year}.{month}{day}"

    # 2. Remove date prefix and leading separators
    rest = stem[date_match.end():]
    rest = rest.lstrip(".,;: '")

    if not rest:
        return result

    # 3. Split off author: find _AGT or fall back to last underscore
    title_pub = rest
    agt_idx = rest.upper().rfind("_AGT")
    if agt_idx >= 0:
        title_pub = rest[:agt_idx]
    else:
        last_us = rest.rfind("_")
        if last_us >= 0:
            title_pub = rest[:last_us]

    if not title_pub:
        title_pub = rest

    # 4. Match publication at the beginning (greedy, longest match first)
    matched_pub = None
    tp_upper = title_pub.upper()
    for pub in _PUBS_SORTED:
        if tp_upper.startswith(pub + ".") or tp_upper.startswith(pub + " "):
            matched_pub = pub
            title_pub = title_pub[len(pub):].lstrip(". ")
            break

    result["publication"] = _normalize_publication(matched_pub)

    # 5. Check for series number (e.g. "01.REFORMA Y REPRESIÓN")
    series_match = re.match(r"^(\d{1,3})[.\s]+(.+)", title_pub)
    if series_match:
        result["series_number"] = int(series_match.group(1))
        title_pub = series_match.group(2)

    result["title"] = _title_case(title_pub.strip()) if title_pub.strip() else stem

    return result


def _normalize_publication(pub: str | None) -> str | None:
    """Clean up publication name for display."""
    if not pub:
        return None
    display = {
        "ABC": "ABC",
        "ACRATAS": "Ácratas",
        "AHORA": "Ahora",
        "AJOBLANCO": "Ajoblanco",
        "ATENEO": "Ateneo",
        "AVUI": "Avui",
        "BLOG AGT": "Blog AGT",
        "DERECHO Y OPINION": "Derecho y Opinión",
        "DIARIO 16": "Diario 16",
        "DIARIORC": "DiarioRC",
        "EL CONFIDENCIAL": "El Confidencial",
        "EL INDEPENDFIENTE": "El Independiente",
        "EL INDEPENDIENTE": "El Independiente",
        "EL MUNDO": "El Mundo",
        "EL PAIS": "El País",
        "EL SIGLO": "El Siglo",
        "ELCORREO": "El Correo",
        "ELDIARIODELAMARINA.COM": "El Diario de la Marina",
        "ELINDEPENDIENTE": "El Independiente",
        "ESPIA EN EL CONGRESO": "Espía en el Congreso",
        "EXTREMADURAPROGRESISTA": "Extremadura Progresista",
        "FILOSOFIA DIGITAL": "Filosofía Digital",
        "IGLESIA VIVA": "Iglesia Viva",
        "L'ESTEL DE MALLORCA": "L'Estel de Mallorca",
        "LA PROVINCIA": "La Provincia",
        "LA RAZON": "La Razón",
        "LAOPINIONDTENERIFE": "La Opinión de Tenerife",
        "MADRID": "Madrid",
        "REPORTER": "Reporter",
        "TEINTERESA": "Te Interesa",
        "YA": "Ya",
        "EL RINCON DE YANKA": "El Rincón de Yanka",
    }
    return display.get(pub.upper(), pub.title())


def _title_case(text: str) -> str:
    """Convert UPPERCASE title to Title Case, preserving short words."""
    if not text.isupper() and not text.islower():
        return text  # already mixed case, leave as-is
    words = text.split()
    small = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "EN", "Y", "A", "AL", "UN", "UNA", "POR", "SIN", "CON", "O", "E", "U", "NI"}
    result = []
    for i, w in enumerate(words):
        if i == 0 or w.upper() not in small:
            result.append(w.capitalize())
        else:
            result.append(w.lower())
    return " ".join(result)


# ── Photo filename parser ─────────────────────────────────────────────

def _make_photo_caption(rest: str) -> str:
    """
    Build a readable caption from the non-date portion of a photo filename.

    Strips author suffixes, sequence numbers, technical codes (IMG_, WA, URLs,
    resolutions), then joins the remaining dot-separated parts with ' · ' and
    applies title case.
    """
    # Strip _AGT suffix (author tag used in article filenames, occasionally in photos)
    agt_idx = rest.upper().rfind("_AGT")
    if agt_idx > 0:
        rest = rest[:agt_idx]
    else:
        # Strip trailing _AUTHOR NAME (underscore followed by 3+ uppercase letters)
        us = rest.rfind("_")
        if us > 0 and re.search(r"[A-Z]{3}", rest[us + 1 :]):
            rest = rest[:us]

    # Split on dots and clean each part
    raw_parts = [p.strip() for p in rest.split(".")]
    clean = []
    for p in raw_parts:
        if not p:
            continue
        if p.lower() == "o":                               # stray letter suffix
            continue
        if re.fullmatch(r"\d+", p):                        # pure number (series, year)
            continue
        if re.fullmatch(r"(IMG|MG)_\d+", p, re.I):        # camera codes IMG_1234
            continue
        if re.fullmatch(r"-?WA\d+", p, re.I):             # WhatsApp codes WA0001
            continue
        if re.fullmatch(r"\d{6,}", p):                     # timestamps 124208
            continue
        if re.search(r"https?:|www\.", p, re.I):           # URLs
            continue
        if re.fullmatch(r"\d+[xX×]\d+", p):               # resolutions 1536x864
            continue
        clean.append(p)

    # Strip trailing standalone sequence number from last part
    if clean:
        last = re.sub(r"\s*[(\[]\d+[)\]]\s*$", "", clean[-1])   # trailing (1) or [1]
        last = re.sub(r"[\s._]+\d+\s*$", "", last).strip()       # trailing .1 or _1
        if last:
            clean[-1] = last
        else:
            clean.pop()

    if not clean:
        return ""

    # Apply title case to uniform-case parts (all-upper or all-lower);
    # leave mixed-case parts as-is (they already have intentional casing).
    titled = []
    for p in clean:
        bare = p.replace(" ", "").replace("-", "")
        titled.append(_title_case(p) if (bare.isupper() or bare.islower()) else p)
    return " · ".join(titled)


def parse_photo_filename(stem: str) -> dict:
    """
    Parse a photo filename stem into date and caption.

    Expected patterns (best-effort, many filenames are informal):
      YYYY.MMDD.CONTEXT.DESCRIPTION.N  →  date=YYYY-MM-DD, caption from rest
      YYYY.MMDD_SOMETHING              →  date=YYYY-MM-DD, caption from rest
      YYYY.CONTEXT.N                   →  date=YYYY, caption from rest
      CONTEXT                          →  date=None, caption from whole stem

    Returns dict with: date (ISO string or None), title (str).
    """
    # Strip _page-NNNN suffix (scans of magazine pages)
    stem = re.sub(r"_page-\d+$", "", stem, flags=re.I)
    # Strip trailing dimension suffixes like -150x150 or -1024x391
    stem = re.sub(r"-\d+[xX]\d+$", "", stem)

    # Try full date: YYYY + sep + MMDD + sep + rest
    # Accept . - _ , or space as separator after MMDD
    m = re.match(r"^(\d{4})[.\- ,](\d{2})(\d{2})[.\-_, ](.+)", stem)
    if m:
        year, month, day, rest = m.groups()
        # Validate month/day to avoid false matches like 1976.1994.1104...
        if int(month) <= 12 and int(day) <= 31:
            if month == "00":
                date = year
            elif day == "00":
                date = f"{year}-{month}"
            else:
                date = f"{year}-{month}-{day}"
            return {"date": date, "title": _make_photo_caption(rest.lstrip(". "))}

    # Try year-only: YYYY + sep + rest
    m = re.match(r"^(\d{4})[.\- ,](.+)", stem)
    if m:
        year, rest = m.groups()
        return {"date": year, "title": _make_photo_caption(rest.lstrip(". "))}

    # No date prefix — clean up the whole stem as caption
    return {"date": None, "title": _make_photo_caption(stem)}


def build_fotos_entries(cids: dict) -> list[dict]:
    """
    Scan FOTOS_DIR and return a catalog entry for each image file.

    Photos are not processed by Docling, so metadata comes entirely from the
    filename. CIDs are looked up directly from ipfs/cids.json using the key
    "fotos/{filename}" (relative to ficheros/publicos/).
    """
    if not FOTOS_DIR.exists():
        print(f"Warning: {FOTOS_DIR} not found — skipping photos")
        return []

    entries = []
    for photo_path in sorted(FOTOS_DIR.iterdir()):
        if not photo_path.is_file():
            continue
        if photo_path.suffix.lower() not in PHOTO_EXTENSIONS:
            continue

        stem = photo_path.stem
        # Strip double extension artefact (e.g. "name.JPG.jpg" → "name")
        # PHOTO_EXTENSIONS contains dotted forms (".jpg"), so prepend "." when comparing.
        if "." in stem and "." + stem.rsplit(".", 1)[1].lower() in PHOTO_EXTENSIONS:
            stem = stem.rsplit(".", 1)[0]

        metadata = parse_photo_filename(stem)

        # CIDs are stored relative to ficheros/publicos/, so key = "fotos/{filename}"
        cid_entry = cids.get(f"fotos/{photo_path.name}")
        ipfs_cid = cid_entry.get("cid") if cid_entry else None

        entries.append({
            "category": "fotos",
            "subcategory": None,
            "title": metadata["title"],
            "date": metadata["date"],
            "date_raw": None,
            "publication": None,
            "series_number": None,
            "has_text": False,
            "markdown_path": None,
            "ipfs_cid": ipfs_cid,
            "ipfs_url": f"{GATEWAY}/{ipfs_cid}" if ipfs_cid else None,
            "source_filename": photo_path.name,
            "slug": slugify(stem) or slugify(photo_path.name),
        })

    return entries


# ── Slug generation ───────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def generate_slug(metadata: dict) -> str:
    """Build a readable, unique-ish slug from article metadata."""
    parts = []
    if metadata.get("date_raw"):
        parts.append(metadata["date_raw"].replace(".", "-"))
    if metadata.get("publication"):
        parts.append(metadata["publication"])
    title = metadata.get("title", "")
    if title:
        # Limit title portion to keep slugs reasonable
        parts.append(title[:80])
    raw = " ".join(parts)
    return slugify(raw) or "untitled"


# ── CID lookup ────────────────────────────────────────────────────────

def find_cid(cids: dict, origin_filename: str, output_rel: Path) -> dict | None:
    """
    Find the IPFS CID for a document.

    Tries multiple path patterns since CID keys are relative to publicos/.
    Prefers .pdf (original scan) over .docx for download link.
    """
    stem = Path(origin_filename).stem
    origin_ext = Path(origin_filename).suffix

    # Build the directory prefix from the output relative path
    # output_rel example: "articulos/1977.0524.REPORTER.01.json"
    # CID key example:    "articulos/1977.0524.REPORTER.01.REFORMA Y REPRESIÓN_AGT.pdf"
    category_dir = str(output_rel.parent)  # e.g. "articulos" or "AGT.HECHOS/1967-GUINEA.AGT"

    # Try exact origin filename
    key = f"{category_dir}/{origin_filename}"
    if key in cids:
        return cids[key]

    # Try PDF version (preferred for download — original scan)
    if origin_ext.lower() != ".pdf":
        pdf_key = f"{category_dir}/{stem}.pdf"
        if pdf_key in cids:
            return cids[pdf_key]

    # Try in DOC/ JPG/ PDF/ subfolders (for articulos)
    for subfolder in ["", "DOC/", "PDF/", "JPG/"]:
        for ext in [".pdf", origin_ext, ".docx", ".jpg", ".JPG"]:
            key = f"{category_dir}/{subfolder}{stem}{ext}"
            if key in cids:
                return cids[key]

    # Fuzzy: search CID keys containing the stem
    for key in cids:
        if stem in key and category_dir in key:
            return cids[key]

    return None


# ── Origin filename reader ────────────────────────────────────────────

def read_origin_filename(json_path: Path) -> str | None:
    """Read origin.filename from a Docling JSON without parsing the full file."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            head = f.read(1000)
        match = re.search(r'"filename"\s*:\s*"([^"]+)"', head)
        return match.group(1) if match else None
    except Exception:
        return None


# ── Main ──────────────────────────────────────────────────────────────

def build_catalog():
    # Load IPFS CIDs
    if CIDS_FILE.exists():
        with open(CIDS_FILE, "r", encoding="utf-8") as f:
            cids = json.load(f)
        print(f"Loaded {len(cids)} CID entries from {CIDS_FILE}")
    else:
        cids = {}
        print(f"Warning: {CIDS_FILE} not found — no IPFS links will be generated")

    # Collect all entries, then deduplicate
    raw_entries = []  # list of (origin_filename, entry_dict, path_depth)
    stats = Counter()

    # Walk output/publicos/ for all Docling JSON files
    for json_path in sorted(OUTPUT_DIR.rglob("*.json")):
        if json_path.name in ("conversion_report.json", "chunking_report.json"):
            continue

        rel = json_path.relative_to(OUTPUT_DIR)

        # Determine category from directory structure
        # articulos/DOC/file.json  → category "articulos", skip DOC/JPG/PDF as subcategory
        # AGT.HECHOS/event/file.json → category "AGT.HECHOS", subcategory "event"
        parts = rel.parts[:-1]  # directory parts, excluding filename
        category = parts[0] if parts else "general"
        subcategory = None

        # For articulos: DOC/JPG/PDF are organizational subfolders, not subcategories
        skip_subfolder = False
        if category == "articulos" and len(parts) > 1 and parts[1] in ("DOC", "JPG", "PDF"):
            skip_subfolder = True

        # For AGT.HECHOS: use only the first-level subfolder as event name
        # (deeper nesting is grouped under the same event)
        if category == "AGT.HECHOS" and len(parts) > 1:
            subcategory = parts[1]

        # Read origin filename from JSON
        origin_filename = read_origin_filename(json_path)
        if not origin_filename:
            stats["no_origin"] += 1
            continue

        # Parse metadata from original filename
        origin_stem = Path(origin_filename).stem
        metadata = parse_filename(origin_stem)

        # Check for companion .md
        md_path = json_path.with_suffix(".md")
        has_text = md_path.exists()
        md_rel = str(md_path.relative_to(OUTPUT_DIR)) if has_text else None

        # Find IPFS CID
        cid_entry = find_cid(cids, origin_filename, rel)
        ipfs_cid = cid_entry.get("cid") if cid_entry else None

        entry = {
            "category": category,
            "subcategory": subcategory,
            "title": metadata["title"],
            "date": metadata["date"],
            "date_raw": metadata["date_raw"],
            "publication": metadata["publication"],
            "series_number": metadata["series_number"],
            "has_text": has_text,
            "markdown_path": md_rel,
            "ipfs_cid": ipfs_cid,
            "ipfs_url": f"{GATEWAY}/{ipfs_cid}" if ipfs_cid else None,
            "source_filename": origin_filename,
        }

        # Depth: prefer root-level entries (fewer path parts) over subfolder copies
        depth = len(rel.parts)
        if skip_subfolder:
            depth += 100  # deprioritize DOC/JPG/PDF copies
        raw_entries.append((origin_filename, entry, depth))
        stats["total"] += 1

    # Deduplicate: keep one entry per origin_filename, preferring shallowest path
    raw_entries.sort(key=lambda x: x[2])  # shallowest first
    seen_origins = set()
    catalog = []
    slugs_seen = Counter()

    for origin_filename, entry, _depth in raw_entries:
        if origin_filename in seen_origins:
            stats["deduped"] += 1
            continue
        seen_origins.add(origin_filename)

        # Generate unique slug
        slug = generate_slug(entry)
        slugs_seen[slug] += 1
        if slugs_seen[slug] > 1:
            slug = f"{slug}-{slugs_seen[slug]}"
        entry["slug"] = slug

        catalog.append(entry)
        stats["ok"] += 1

    # Pass 2: photos (scanned directly from ficheros/publicos/fotos/)
    fotos = build_fotos_entries(cids)
    catalog.extend(fotos)

    # Sort by category then date
    catalog.sort(key=lambda x: (x["category"], x.get("date") or "0000"))

    # Write catalog
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    # Print summary
    categories = Counter(item["category"] for item in catalog)
    pubs = Counter(item["publication"] for item in catalog if item["publication"])
    has_text_count = sum(1 for item in catalog if item["has_text"])
    has_cid_count = sum(1 for item in catalog if item["ipfs_cid"])

    fotos_with_cid = sum(1 for f in fotos if f["ipfs_cid"])

    print(f"\nCatalog written to {CATALOG_FILE}")
    print(f"  Total items:  {len(catalog)}")
    print(f"  With text:    {has_text_count}")
    print(f"  With IPFS:    {has_cid_count}")
    print(f"  Deduplicated: {stats['deduped']}")
    print(f"  No origin:    {stats['no_origin']}")
    print(f"\nPhotos: {len(fotos)} found, {fotos_with_cid} with IPFS CIDs")
    print(f"\nCategories:")
    for cat, count in categories.most_common():
        print(f"  {cat}: {count}")
    print(f"\nTop publications:")
    for pub, count in pubs.most_common(10):
        print(f"  {pub}: {count}")


if __name__ == "__main__":
    build_catalog()
