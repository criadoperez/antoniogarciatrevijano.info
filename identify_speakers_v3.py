#!/usr/bin/env python3
"""
identify_speakers_v3.py — Speaker identification using multiple signal layers.

Signals:
1. Self-identification: "soy X", "les habla X" → we know exactly who that speaker is
2. Host detection: "Buenos días queridos oyentes, bienvenidos a RLC" → host, not AGT
3. Description parsing: named participants from .info.json
4. Address patterns: "don Antonio" in someone's line → they are NOT AGT
5. Word count dominance: AGT typically speaks the most
6. Content cues: AGT discusses constitutional theory, uses formal philosophical language

Other speaker identification (conservative):
A. Self-identification of non-AGT speakers (highest confidence)
B. Elimination: AGT + hosts known, 1 remaining speaker = 1 named participant in description
"""

import json
import re
from pathlib import Path

SUMMARIES = Path(__file__).parent / "speaker_summaries.json"
OUTPUT = Path(__file__).parent / "speaker_decisions.json"

AGT = "Antonio García-Trevijano"

# ── Known persons for description parsing ──────────────────────────────

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
    (r"José\s+María\s+Fernández[- ]?Isla", "José María Fernández Isla"),
    (r"Fernando\s+Caro", "Fernando Caro"),
    (r"David\s+López", "David López"),
    (r"Helena\s+Bazán", "Helena Bazán"),
    (r"Luis\s+Zayas", "Luis Zayas"),
    (r"Leopoldo\s+Gonzalo", "Leopoldo Gonzalo"),
    (r"Agustín\s+", "Agustín"),
    (r"Miguel\s+Ángel\s+Alonso", "Miguel Ángel Alonso"),
    (r"Ángel\s+Gimeno", "Ángel Gimeno"),
    (r"Lorenzo\s+Alonso", "Lorenzo Alonso"),
    (r"José\s+Escandell", "José Escandell"),
]

TECH_PATTERN = re.compile(
    r"colaboraci[oó]n\s+t[eé]cnica|presenciado\s+el\s+programa|realizaci[oó]n",
    re.IGNORECASE,
)

# ── Signal patterns ────────────────────────────────────────────────────

# Self-identification: speaker announces who they are
SELF_ID_PATTERNS = [
    (r"(?:les|os)\s+habla\s+(\w[\w\s]+?)(?:\.|,|y\s)", None),  # "les habla Daniel Sancho"
    (r"soy\s+(\w[\w\s]+?)(?:\.|,|\s+y\s)", None),               # "soy Adrián Perales"
]

# Host/presenter patterns — person saying this is the host, NOT AGT
HOST_PATTERNS = re.compile(
    r"[Bb]uenos\s+d[ií]as\s+(?:queridos\s+)?oyentes|"
    r"[Bb]ienvenidos\s+a\s+(?:una?\s+nuev[oa])?\s*(?:emisi[oó]n|programa)|"
    r"Radio\s+Libertad\s+Constituyente|"
    r"[Cc]ontinuamos\s+en\s+(?:el\s+)?(?:107|Libertad)|"
    r"va\s+a\s+dar\s+comienzo\s+el\s+debate|"
    r"[Ee]nseguida\s+volvemos|"
    r"[Cc]ontinuamos,?\s+queridos",
)

# Address patterns — speaker saying these is addressing AGT (so they are NOT AGT)
AGT_ADDRESS_RE = re.compile(
    r"[Dd]on\s+Antonio(?:\s|,|\.|$)|"
    r"García[- ]Trevijano|"
    r"Trevijano(?:\s|,|\.|$)",
)

# Broader check: is AGT mentioned at all in mentions?
AGT_MENTION_RE = re.compile(r"Antonio|Trevijano|maestro", re.IGNORECASE)

# Description check for AGT presence
AGT_DESC_RE = re.compile(r"García[- ]Trevijano|Trevijano", re.IGNORECASE)


def parse_participants(description):
    if not description:
        return [], []
    parts = TECH_PATTERN.split(description, maxsplit=1)
    main_text = parts[0]
    tech_text = parts[1] if len(parts) > 1 else ""
    participants, tech = [], []
    for pattern, name in KNOWN_PERSONS:
        if re.search(pattern, main_text, re.IGNORECASE):
            if name not in participants:
                participants.append(name)
        elif re.search(pattern, tech_text, re.IGNORECASE):
            if name not in tech:
                tech.append(name)
    return participants, tech


def detect_hosts(name_mentions, first_lines):
    """Detect which speakers are hosts/presenters (NOT AGT)."""
    hosts = set()
    # Check name_mentions
    for line in name_mentions:
        m = re.match(r"\[(\w+)\]\s*(.*)", line)
        if m:
            spk, text = m.group(1), m.group(2)
            if HOST_PATTERNS.search(text):
                hosts.add(spk)
    # Check first_lines
    for spk, lines in first_lines.items():
        if spk == "DESCONOCIDO":
            continue
        for text in lines:
            if HOST_PATTERNS.search(text):
                hosts.add(spk)
    return hosts


def detect_addressers(name_mentions, first_lines):
    """Detect speakers who address AGT by name (they are NOT AGT)."""
    addressers = set()
    for line in name_mentions:
        m = re.match(r"\[(\w+)\]\s*(.*)", line)
        if m:
            spk, text = m.group(1), m.group(2)
            if AGT_ADDRESS_RE.search(text):
                addressers.add(spk)
    for spk, lines in first_lines.items():
        if spk == "DESCONOCIDO":
            continue
        for text in lines:
            if AGT_ADDRESS_RE.search(text):
                addressers.add(spk)
    return addressers


def detect_self_identified(name_mentions, first_lines):
    """Detect speakers who identify themselves by name. Returns {speaker: name}."""
    identified = {}
    all_lines = []
    for line in name_mentions:
        m = re.match(r"\[(\w+)\]\s*(.*)", line)
        if m:
            all_lines.append((m.group(1), m.group(2)))
    for spk, lines in first_lines.items():
        if spk == "DESCONOCIDO":
            continue
        for text in lines:
            all_lines.append((spk, text))

    for spk, text in all_lines:
        for known_re, known_name in KNOWN_PERSONS:
            # Check "les habla X" or "soy X" patterns
            for pat in [r"(?:les|os)\s+habla\s+", r"\bsoy\s+"]:
                full = pat + known_re
                if re.search(full, text, re.IGNORECASE):
                    identified[spk] = known_name
                    break
    return identified


def agt_present_in_mentions(name_mentions):
    """Check if AGT is mentioned/addressed anywhere in the transcript excerpts."""
    for line in name_mentions:
        if AGT_MENTION_RE.search(line):
            return True
    return False


def analyze_episode(ep):
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

    total_words = sum(word_counts.get(s, 0) for s in main_speakers)
    if total_words == 0:
        return {"id": eid, "assignments": None, "confidence": None,
                "reason": "no words"}

    word_shares = {s: word_counts.get(s, 0) / total_words for s in main_speakers}
    ranked = sorted(
        [(s, word_counts.get(s, 0)) for s in main_speakers],
        key=lambda x: x[1], reverse=True,
    )

    # ── Gather signals ─────────────────────────────────────────────────

    participants, tech_crew = parse_participants(description)
    agt_in_desc = AGT in participants
    agt_in_title = bool(re.search(r"Trevijano|García.Trevijano", title, re.IGNORECASE))
    agt_in_mentions = agt_present_in_mentions(name_mentions)
    agt_present = agt_in_desc or agt_in_title or agt_in_mentions

    if not agt_present:
        return {"id": eid, "assignments": None, "confidence": None,
                "reason": "no evidence AGT is in this episode"}

    hosts = detect_hosts(name_mentions, first_lines)
    addressers = detect_addressers(name_mentions, first_lines)
    self_identified = detect_self_identified(name_mentions, first_lines)

    # Eliminate: hosts, addressers, self-identified non-AGT speakers
    not_agt = set()
    not_agt.update(hosts)
    not_agt.update(addressers)
    for spk, name in self_identified.items():
        if name != AGT:
            not_agt.add(spk)
        else:
            # Speaker self-identified as AGT!
            return {"id": eid,
                    "assignments": {spk: AGT},
                    "confidence": "high",
                    "reason": f"self-identified as AGT"}

    candidates = [s for s in main_speakers if s not in not_agt]
    reasons_parts = []

    # ── Decision cases ─────────────────────────────────────────────────

    # Case 1: Exactly one candidate left after elimination
    if len(candidates) == 1:
        spk = candidates[0]
        words = word_counts.get(spk, 0)
        share = word_shares.get(spk, 0)
        eliminated = not_agt & set(main_speakers)
        if words >= 100:
            conf = "high" if (len(eliminated) >= 2 or (agt_in_desc and len(eliminated) >= 1)) else "medium"
            return {"id": eid,
                    "assignments": {spk: AGT},
                    "confidence": conf,
                    "reason": f"only candidate after eliminating {eliminated}; "
                              f"{spk} speaks {words} words ({share:.0%})"}
        elif words >= 30 and share >= 0.05:
            # Low word count but only candidate — medium confidence
            return {"id": eid,
                    "assignments": {spk: AGT},
                    "confidence": "medium",
                    "reason": f"only candidate after eliminating {eliminated}; "
                              f"{spk} speaks {words} words ({share:.0%}) — low volume"}

    # Case 2: Two main speakers, AGT in desc, no other named participant
    other_participants = [p for p in participants if p != AGT]
    if agt_in_desc and len(main_speakers) == 2 and len(other_participants) == 0:
        best = ranked[0]
        return {"id": eid,
                "assignments": {best[0]: AGT},
                "confidence": "high",
                "reason": f"only named participant, 2 speakers, "
                          f"{best[0]} speaks most ({best[1]} words, {word_shares[best[0]]:.0%})"}

    # Case 3: Multiple candidates — pick dominant non-eliminated speaker
    if len(candidates) >= 2:
        cand_ranked = sorted(
            [(s, word_counts.get(s, 0)) for s in candidates],
            key=lambda x: x[1], reverse=True,
        )
        best = cand_ranked[0]
        share = word_shares.get(best[0], 0)

        # Strong dominance among candidates
        if share > 0.35 and best[1] > 200:
            # Check if runner-up is much smaller (2x gap)
            runner_up = cand_ranked[1][1] if len(cand_ranked) > 1 else 0
            gap_ratio = best[1] / max(runner_up, 1)
            if gap_ratio >= 1.8 or share > 0.45:
                conf = "high" if (agt_in_desc and gap_ratio >= 2.5) else "medium"
                return {"id": eid,
                        "assignments": {best[0]: AGT},
                        "confidence": conf,
                        "reason": f"{len(candidates)} candidates, {best[0]} dominant with "
                                  f"{best[1]} words ({share:.0%}), "
                                  f"gap ratio {gap_ratio:.1f}x over next"}

        # Moderate dominance with description support
        if agt_in_desc and share > 0.25 and best[1] > 300:
            runner_up = cand_ranked[1][1] if len(cand_ranked) > 1 else 0
            gap_ratio = best[1] / max(runner_up, 1)
            if gap_ratio >= 1.5:
                return {"id": eid,
                        "assignments": {best[0]: AGT},
                        "confidence": "medium",
                        "reason": f"{len(candidates)} candidates, {best[0]} leads with "
                                  f"{best[1]} words ({share:.0%}), gap {gap_ratio:.1f}x, "
                                  f"AGT in description"}

    # Not enough evidence
    reason = f"{len(candidates)} candidates from {len(main_speakers)} speakers"
    if not agt_in_desc:
        reason += "; AGT not in description (only in mentions)"
    if candidates:
        cand_info = [(s, word_counts.get(s, 0)) for s in candidates]
        reason += f"; candidates: {cand_info}"
    return {"id": eid, "assignments": None, "confidence": None, "reason": reason}


# ── Other speaker identification (conservative) ───────────────────────


def identify_other_speakers(ep, agt_decision):
    """Conservatively identify non-AGT speakers using two strategies:

    Strategy A — Self-identification: speaker says "les habla X" / "soy X"
                 and matches a known person. Highest confidence.

    Strategy B — Elimination: AGT and hosts are known, exactly 1 remaining
                 main speaker matches exactly 1 other named participant
                 in the episode description.

    Returns dict of {locutor_label: {"name": str, "confidence": str, "strategy": str}}
    """
    others = {}

    speakers = ep.get("speakers", [])
    main_speakers = [s for s in speakers if s != "DESCONOCIDO"]
    word_counts = ep.get("word_counts", {})
    first_lines = ep.get("first_lines", {})
    name_mentions = ep.get("name_mentions", [])
    description = ep.get("description", "")

    # Determine which speaker label is AGT (if identified)
    agt_label = None
    if agt_decision.get("assignments"):
        for label, name in agt_decision["assignments"].items():
            if name == AGT:
                agt_label = label
                break

    # ── Strategy A: Self-identification ────────────────────────────────
    self_identified = detect_self_identified(name_mentions, first_lines)
    for spk, name in self_identified.items():
        if name != AGT and spk != agt_label and spk in main_speakers:
            others[spk] = {
                "name": name,
                "confidence": "high",
                "strategy": "self-identification",
            }

    # ── Strategy B: Elimination (requires AGT to be identified) ───────
    if agt_label:
        participants, tech_crew = parse_participants(description)
        other_participants = [p for p in participants if p != AGT]

        hosts = detect_hosts(name_mentions, first_lines)

        # Speakers remaining after removing AGT, hosts, and already-identified
        known_labels = {agt_label} | hosts | set(others.keys())
        remaining_speakers = [s for s in main_speakers if s not in known_labels]

        if len(remaining_speakers) == 1 and len(other_participants) == 1:
            spk = remaining_speakers[0]
            name = other_participants[0]
            words = word_counts.get(spk, 0)
            # Safety: speaker must have spoken a meaningful amount
            if words >= 50:
                others[spk] = {
                    "name": name,
                    "confidence": "high",
                    "strategy": "elimination",
                }

    return others


def main():
    print("Loading summaries...")
    with open(SUMMARIES) as f:
        episodes = json.load(f)
    print(f"Loaded {len(episodes)} episodes.\n")

    decisions = []
    other_stats = {"self-identification": 0, "elimination": 0}
    other_by_name = {}

    for ep in episodes:
        decision = analyze_episode(ep)

        # Try to identify other speakers
        other_ids = identify_other_speakers(ep, decision)
        if other_ids:
            if decision["assignments"] is None:
                decision["assignments"] = {}
            for spk, info in other_ids.items():
                decision["assignments"][spk] = info["name"]
                other_stats[info["strategy"]] += 1
                other_by_name[info["name"]] = other_by_name.get(info["name"], 0) + 1
            # Store metadata for reference (not used by apply script)
            decision["other_identifications"] = {
                spk: info for spk, info in other_ids.items()
            }

        decisions.append(decision)

    OUTPUT.write_text(
        json.dumps(decisions, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )

    identified = [d for d in decisions if d["assignments"]]
    agt_identified = [d for d in decisions if d.get("assignments") and AGT in d["assignments"].values()]
    high = [d for d in agt_identified if d["confidence"] == "high"]
    medium = [d for d in agt_identified if d["confidence"] == "medium"]
    unid = [d for d in decisions if not d.get("assignments") or AGT not in d.get("assignments", {}).values()]
    has_others = [d for d in decisions if d.get("other_identifications")]

    print(f"{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total episodes:          {len(episodes)}")
    print(f"AGT identified:          {len(agt_identified)} ({len(agt_identified)/len(episodes):.1%})")
    print(f"  High confidence:       {len(high)}")
    print(f"  Medium confidence:     {len(medium)}")
    print(f"AGT not identified:      {len(unid)}")
    print()

    reasons = {}
    for d in decisions:
        if d.get("assignments") and AGT in d["assignments"].values():
            continue
        r = d["reason"]
        if "no evidence AGT" in r:
            key = "no evidence AGT is in episode"
        elif "candidates from" in r:
            key = "multiple candidates, can't distinguish"
        elif "fewer than 2" in r:
            key = "fewer than 2 speakers"
        else:
            key = r[:60]
        reasons[key] = reasons.get(key, 0) + 1
    print("Unidentified AGT breakdown:")
    for r, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {count:4d}  {r}")
    print()

    print(f"{'='*60}")
    print(f"OTHER SPEAKERS")
    print(f"{'='*60}")
    total_others = sum(other_stats.values())
    print(f"Episodes with other IDs: {len(has_others)}")
    print(f"Total other IDs:         {total_others}")
    print(f"  Self-identification:   {other_stats['self-identification']}")
    print(f"  Elimination:           {other_stats['elimination']}")
    print()
    if other_by_name:
        print("By person:")
        for name, count in sorted(other_by_name.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}  {name}")
        print()

    # Show samples
    print("Sample OTHER identifications:")
    shown = 0
    for d in decisions:
        if d.get("other_identifications") and shown < 10:
            for spk, info in d["other_identifications"].items():
                print(f"  {d['id']}: {spk} → {info['name']}  [{info['strategy']}]")
            shown += 1
    print()

    print(f"Written to {OUTPUT}")


if __name__ == "__main__":
    main()
