#!/usr/bin/env python3
"""
identify_speakers_v2.py — Smart speaker identification using metadata + text analysis.

Reads episode summaries (speaker_summaries.json), analyzes each episode using
multiple signals, and writes speaker_decisions.json with confident identifications.

Signals used:
1. Description parsing — who participated (from .info.json)
2. Name addressing — who addresses "Don Antonio" / "Trevijano" (they are NOT AGT)
3. Word count dominance — AGT typically speaks the most
4. Content analysis — AGT discusses political theory, democracy, constitutional law
5. Speaker role — the host/presenter opens the show, introduces, moderates

Usage:
    python identify_speakers_v2.py
"""

import json
import re
from pathlib import Path

SUMMARIES = Path(__file__).parent / "speaker_summaries.json"
OUTPUT = Path(__file__).parent / "speaker_decisions.json"

AGT = "Antonio García-Trevijano"

# ── Description parsing ────────────────────────────────────────────────

KNOWN_PERSONS = [
    (r"Antonio\s+García[- ]Trevijano(?:\s+Forte)?", AGT),
    (r"Antonio\s+García\s+Paredes", "Antonio García Paredes"),
    (r"Roberto\s+Centeno", "Roberto Centeno"),
    (r"Adrián\s+Perales(?:\s+Pina)?", "Adrián Perales"),
    (r"Dalmacio\s+Negro", "Dalmacio Negro"),
    (r"Daniel\s+Sancho", "Daniel Sancho"),
    (r"Jorge\s+Sánchez\s+de\s+Castro", "Jorge Sánchez de Castro"),
    (r"José\s+Papí", "José Papí"),
    (r"Pedro\s+Gallego", "Pedro Gallego"),
    (r"Hilario\s+García", "Hilario García"),
    (r"Gabriel\s+Albiac", "Gabriel Albiac"),
    (r"Jesús\s+Murciego", "Jesús Murciego"),
    (r"Pedro\s+M(?:aría|\.)\s+González", "Pedro María González"),
    (r"Martín\s+Alonso", "Martín Alonso"),
    (r"José\s+María\s+Fernández\s+Isla", "José María Fernández Isla"),
    (r"Fernando\s+Caro", "Fernando Caro"),
    (r"David\s+López", "David López"),
    (r"Helena\s+Bazán", "Helena Bazán"),
]

TECH_PATTERN = re.compile(
    r"colaboraci[oó]n\s+t[eé]cnica|presenciado|estudio|realizaci[oó]n",
    re.IGNORECASE,
)

# Patterns that address AGT — speaker who says these is NOT AGT
AGT_ADDRESS_RE = re.compile(
    r"(?:Don |don )?Antonio(?:\s|,|\.|$)|"
    r"(?:Don |don )?García[- ]?Trevijano|"
    r"Trevijano|"
    r"(?:el |al )maestro",
)

# AGT's typical vocabulary — used to identify his speech by content
AGT_CONTENT_WORDS = re.compile(
    r"democracia\s+formal|libertad\s+constituyente|"
    r"monarqu[ií]a\s+de\s+partidos|r[eé]gimen|"
    r"rep[uú]blica\s+constitucional|constituci[oó]n|"
    r"soberan[ií]a|pueblo\s+espa[ñn]ol|"
    r"democracia\s+pol[ií]tica|separaci[oó]n\s+de\s+poderes|"
    r"oligarqu[ií]a|transici[oó]n|"
    r"estado\s+de\s+partidos|partitocracia",
    re.IGNORECASE,
)

# Description patterns indicating AGT is present
AGT_DESC_RE = re.compile(r"García[- ]Trevijano|Trevijano", re.IGNORECASE)


def parse_participants(description: str) -> tuple[list[str], list[str]]:
    """Extract (main_participants, tech_crew) from description."""
    if not description:
        return [], []

    parts = TECH_PATTERN.split(description, maxsplit=1)
    main_text = parts[0]
    tech_text = parts[1] if len(parts) > 1 else ""

    participants = []
    tech = []
    for pattern, name in KNOWN_PERSONS:
        if re.search(pattern, main_text, re.IGNORECASE):
            if name not in participants:
                participants.append(name)
        elif re.search(pattern, tech_text, re.IGNORECASE):
            if name not in tech:
                tech.append(name)

    return participants, tech


def analyze_episode(ep: dict) -> dict:
    """Analyze a single episode and return a decision."""
    eid = ep["id"]
    speakers = ep.get("speakers", [])
    word_counts = ep.get("word_counts", {})
    first_lines = ep.get("first_lines", {})
    name_mentions = ep.get("name_mentions", [])
    description = ep.get("description", "")
    title = ep.get("title", "")

    main_speakers = [s for s in speakers if s != "DESCONOCIDO"]
    if len(main_speakers) < 2:
        return {"id": eid, "assignments": None, "confidence": None,
                "reason": "fewer than 2 main speakers"}

    # Parse description
    participants, tech_crew = parse_participants(description)
    agt_in_desc = AGT in participants
    agt_in_title = bool(re.search(r"Trevijano|García.Trevijano", title, re.IGNORECASE))
    agt_mentioned = agt_in_desc or agt_in_title or bool(AGT_DESC_RE.search(description))

    # Rank speakers by word count
    ranked = sorted(
        [(s, word_counts.get(s, 0)) for s in main_speakers],
        key=lambda x: x[1], reverse=True,
    )
    total_words = sum(word_counts.get(s, 0) for s in main_speakers)
    if total_words == 0:
        return {"id": eid, "assignments": None, "confidence": None,
                "reason": "no word counts available"}

    # Signal 1: Who addresses AGT by name? They are NOT AGT.
    addressers = set()
    for line in name_mentions:
        m = re.match(r"\[(\w+)\]\s*(.*)", line)
        if m:
            spk, text = m.group(1), m.group(2)
            if AGT_ADDRESS_RE.search(text):
                addressers.add(spk)

    # Signal 2: Content analysis — whose first lines sound like AGT?
    agt_content_scores = {}
    for spk, lines in first_lines.items():
        if spk == "DESCONOCIDO":
            continue
        text = " ".join(lines)
        matches = len(AGT_CONTENT_WORDS.findall(text))
        agt_content_scores[spk] = matches

    # Signal 3: Word count share
    word_shares = {s: word_counts.get(s, 0) / total_words for s in main_speakers}

    # ── Decision logic ─────────────────────────────────────────────────

    # Eliminate addressers (they are NOT AGT)
    candidates = [s for s in main_speakers if s not in addressers]
    non_candidates = addressers

    # Case 1: HIGH — AGT in desc, only 2 main speakers, one addresses AGT
    if agt_mentioned and len(main_speakers) == 2 and len(addressers) == 1:
        agt_speaker = [s for s in main_speakers if s not in addressers][0]
        return {"id": eid,
                "assignments": {agt_speaker: AGT},
                "confidence": "high",
                "reason": f"2 speakers, {list(addressers)[0]} addresses AGT, "
                          f"so {agt_speaker} is AGT ({word_counts.get(agt_speaker,0)} words)"}

    # Case 2: HIGH — AGT in desc, only 2 main speakers, AGT is only named participant
    other_participants = [p for p in participants if p != AGT]
    if agt_in_desc and len(main_speakers) == 2 and len(other_participants) == 0:
        # The speaker with more words is AGT (host introduces, AGT talks)
        best = ranked[0]
        return {"id": eid,
                "assignments": {best[0]: AGT},
                "confidence": "high",
                "reason": f"only named participant, 2 speakers, "
                          f"{best[0]} speaks most ({best[1]} words, {word_shares[best[0]]:.0%})"}

    # Case 3: HIGH — addressers eliminate all but one candidate, AGT mentioned
    # But require minimum 5% word share to avoid false positives
    if agt_mentioned and len(candidates) == 1:
        agt_speaker = candidates[0]
        words = word_counts.get(agt_speaker, 0)
        share = word_shares.get(agt_speaker, 0)
        if words > 100 and share > 0.05:
            return {"id": eid,
                    "assignments": {agt_speaker: AGT},
                    "confidence": "high",
                    "reason": f"only non-addressing speaker, AGT in desc, "
                              f"{agt_speaker} speaks {words} words ({share:.0%})"}

    # Case 4: HIGH — AGT in desc, 3 speakers, one is known other participant,
    # one addresses AGT → remaining is AGT
    if agt_in_desc and len(main_speakers) == 3 and len(other_participants) >= 1:
        # If we can identify who the other participant is, AGT is the remaining
        if len(addressers) >= 1 and len(candidates) <= 2:
            # Among candidates, pick the one with most words
            cand_ranked = sorted(
                [(s, word_counts.get(s, 0)) for s in candidates],
                key=lambda x: x[1], reverse=True,
            )
            if cand_ranked and cand_ranked[0][1] / total_words > 0.20:
                best = cand_ranked[0]
                return {"id": eid,
                        "assignments": {best[0]: AGT},
                        "confidence": "high",
                        "reason": f"3 speakers, {addressers} address AGT, "
                                  f"{best[0]} speaks {best[1]} words ({word_shares[best[0]]:.0%})"}

    # Case 5: MEDIUM — AGT mentioned, dominant speaker (>35% words among candidates)
    if agt_mentioned and len(candidates) > 0:
        # Among candidates (non-addressers), find the dominant one
        cand_ranked = sorted(
            [(s, word_counts.get(s, 0)) for s in candidates],
            key=lambda x: x[1], reverse=True,
        )
        best = cand_ranked[0]
        share = word_shares.get(best[0], 0)
        if share > 0.35 and best[1] > 200:
            return {"id": eid,
                    "assignments": {best[0]: AGT},
                    "confidence": "medium",
                    "reason": f"AGT mentioned, {best[0]} dominant with "
                              f"{best[1]} words ({share:.0%}), not an addresser"}

    # Case 6: MEDIUM — AGT mentioned, no addressers but content matches
    if agt_mentioned and not addressers:
        # Use word count + content score
        best_content = max(main_speakers, key=lambda s: agt_content_scores.get(s, 0))
        best_words = ranked[0][0]
        if best_content == best_words and word_shares.get(best_words, 0) > 0.30:
            return {"id": eid,
                    "assignments": {best_words: AGT},
                    "confidence": "medium",
                    "reason": f"AGT mentioned, {best_words} speaks most "
                              f"({ranked[0][1]} words, {word_shares[best_words]:.0%}) "
                              f"and has AGT-like content"}

    # Case 7: MEDIUM — AGT mentioned, 4+ speakers but one clearly dominates
    if agt_mentioned and len(main_speakers) >= 4:
        best = ranked[0]
        share = word_shares.get(best[0], 0)
        if share > 0.35 and best[0] not in addressers and best[1] > 200:
            return {"id": eid,
                    "assignments": {best[0]: AGT},
                    "confidence": "medium",
                    "reason": f"AGT mentioned, {len(main_speakers)} speakers, "
                              f"{best[0]} dominates with {best[1]} words ({share:.0%})"}

    # Case 8: MEDIUM — no description but address pattern clearly identifies
    if not agt_mentioned and len(addressers) >= 2:
        # Multiple speakers address AGT → strong signal even without description
        if len(candidates) == 1:
            agt_speaker = candidates[0]
            words = word_counts.get(agt_speaker, 0)
            if words > 0 and word_shares.get(agt_speaker, 0) > 0.15:
                return {"id": eid,
                        "assignments": {agt_speaker: AGT},
                        "confidence": "medium",
                        "reason": f"no desc but {len(addressers)} speakers address AGT, "
                                  f"only candidate is {agt_speaker} ({words} words)"}

    # Not confident enough
    reason_parts = []
    if not agt_mentioned:
        reason_parts.append("AGT not mentioned in desc/title")
    if len(main_speakers) > 4:
        reason_parts.append(f"{len(main_speakers)} speakers")
    if not addressers:
        reason_parts.append("no address patterns found")
    if addressers and len(candidates) > 1:
        reason_parts.append(f"multiple candidates: {candidates}")
    reason = "; ".join(reason_parts) if reason_parts else "insufficient evidence"

    return {"id": eid, "assignments": None, "confidence": None, "reason": reason}


def main():
    print("Loading summaries...")
    with open(SUMMARIES) as f:
        episodes = json.load(f)
    print(f"Loaded {len(episodes)} episodes.\n")

    decisions = []
    for ep in episodes:
        decisions.append(analyze_episode(ep))

    # Write decisions
    OUTPUT.write_text(
        json.dumps(decisions, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )

    # Stats
    identified = [d for d in decisions if d["assignments"]]
    high = [d for d in identified if d["confidence"] == "high"]
    medium = [d for d in identified if d["confidence"] == "medium"]
    unid = [d for d in decisions if not d["assignments"]]

    print(f"{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total episodes:          {len(episodes)}")
    print(f"AGT identified:          {len(identified)} ({len(identified)/len(episodes):.1%})")
    print(f"  High confidence:       {len(high)}")
    print(f"  Medium confidence:     {len(medium)}")
    print(f"Not identified:          {len(unid)}")
    print()

    # Breakdown of why not identified
    reasons = {}
    for d in unid:
        r = d["reason"]
        key = r.split(";")[0].strip() if ";" in r else r
        reasons[key] = reasons.get(key, 0) + 1
    print("Unidentified reasons:")
    for r, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {count:4d}  {r}")
    print()

    # Samples
    print("Sample HIGH identifications:")
    for d in high[:5]:
        a = list(d["assignments"].items())[0]
        print(f"  {d['id']}: {a[0]} → {a[1]}")
        print(f"    {d['reason']}")
    print()
    print("Sample MEDIUM identifications:")
    for d in medium[:5]:
        a = list(d["assignments"].items())[0]
        print(f"  {d['id']}: {a[0]} → {a[1]}")
        print(f"    {d['reason']}")
    print()
    print(f"Decisions written to {OUTPUT}")


if __name__ == "__main__":
    main()
