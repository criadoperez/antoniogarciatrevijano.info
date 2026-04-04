# Speaker Identification in Audio Transcripts

## Summary

1,959 audio episodes from Radio Libertad Constituyente were processed to replace anonymous speaker labels (`LOCUTOR_00`, `LOCUTOR_01`, etc.) with real names — primarily **Antonio García-Trevijano** (AGT).

**Result: 1,580 of 1,959 episodes (80.7%) now have AGT identified by name.**

| Category | Count | % |
|----------|-------|---|
| AGT identified (total) | 1,580 | 80.7% |
| — High confidence | 861 | |
| — Medium confidence | 719 | |
| Not identified | 379 | 19.3% |
| — AGT not in episode | ~87 | |
| — Ambiguous / needs voice analysis | ~280 | |
| — Single-speaker diarization issues | ~12 | |

## How It Works

The pipeline has three stages:

### Stage 1: Extract Summaries (`extract_speaker_summaries.py`)

Reads all `.md` and `.info.json` files from `ficheros/publicos/audios/` and produces a compact JSON file (`speaker_summaries.json`) with per-episode data:

- Episode ID, title, description (from iVoox metadata)
- Speaker labels and word counts per speaker
- First 2 lines spoken by each speaker
- Lines containing name mentions (Don Antonio, Trevijano, etc.)

```bash
cd /root/antoniogarciatrevijano.info
python3 tools/audio-transcripts/extract_speaker_summaries.py
# Output: speaker_summaries.json (~7 MB)
```

### Stage 2: Identify Speakers (`identify_speakers_v3.py`)

Analyzes each episode using multiple signal layers to determine which `LOCUTOR_XX` is AGT:

**Signals used (in priority order):**

1. **Self-identification** — "les habla X", "soy X" in a speaker's line tells us exactly who they are
2. **Host detection** — "Buenos días queridos oyentes, bienvenidos a Radio Libertad Constituyente" = host, NOT AGT
3. **Description parsing** — "Han intervenido Don X y Don Antonio García-Trevijano" names participants
4. **Address patterns** — if a speaker says "Don Antonio" or "Trevijano", they are addressing AGT (so they are NOT AGT)
5. **Elimination** — after removing hosts, self-identified non-AGT speakers, and addressers, if only one candidate remains, that's AGT
6. **Word count dominance** — among remaining candidates, the speaker with the most words and a clear gap over the next candidate is likely AGT

**Decision logic:**
- **High confidence**: only one candidate remains after elimination, or 2 speakers with AGT as only named participant
- **Medium confidence**: AGT mentioned in description and one candidate dominates with >35% of total words and >1.5x gap over the next candidate

```bash
python3 tools/audio-transcripts/identify_speakers_v3.py
# Output: speaker_decisions.json
```

### Stage 2b: AI Review (manual)

For episodes the script couldn't resolve (411 initially), an AI (Claude) reviewed the episode summaries and made additional identifications by understanding:

- Merged transcripts where host introductions got combined with AGT's speech
- Solo monologues where only 1 speaker existed (script required 2+)
- AGT patterns: "aquí estoy esperando" after being introduced, greeting other guests by first name, quoting philosophers (Schopenhauer, Thomas Mann), constitutional law references
- "D. Antonio" in descriptions (without "García-Trevijano")

This added 32 more identifications to the 1,548 from the script.

### Stage 3: Apply Decisions (`apply_speaker_decisions.py`)

Reads `speaker_decisions.json` and applies the identifications to the actual `.md` and `.srt` files:

1. Renames originals to `.sinidentificar.md` / `.sinidentificar.srt` (backup)
2. Creates new `.md` / `.srt` with `LOCUTOR_XX` replaced by `Antonio García-Trevijano`
3. Updates the `speakers` list in the YAML frontmatter

```bash
python3 tools/audio-transcripts/apply_speaker_decisions.py          # Dry run
python3 tools/audio-transcripts/apply_speaker_decisions.py --apply  # Apply changes
python3 tools/audio-transcripts/apply_speaker_decisions.py --apply --force  # Redo all
```

**Safety**: originals are never overwritten. They are renamed to `.sinidentificar.md/.srt` first. To revert, delete the new files and rename the `.sinidentificar.*` files back.

## File Changes

After running the pipeline:

```
ficheros/publicos/audios/
  10004999.md                    ← New: LOCUTOR_02 → "Antonio García-Trevijano"
  10004999.sinidentificar.md     ← Original backup
  10004999.srt                   ← New: [LOCUTOR_02] → [Antonio García-Trevijano]
  10004999.sinidentificar.srt    ← Original backup
  10004999.mp3                   ← Unchanged
  10004999.info.json             ← Unchanged
```

## Running the Full Pipeline

```bash
cd /root/antoniogarciatrevijano.info

# 1. Extract summaries
python3 tools/audio-transcripts/extract_speaker_summaries.py

# 2. Run identification
python3 tools/audio-transcripts/identify_speakers_v3.py

# 3. Review speaker_decisions.json if desired

# 4. Apply
python3 tools/audio-transcripts/apply_speaker_decisions.py --apply
```

## Re-running After Changes

If transcripts are re-generated (e.g., new diarization):

```bash
# Remove backups to allow re-processing
# (the --force flag re-processes from .sinidentificar originals if they exist)
python3 tools/audio-transcripts/apply_speaker_decisions.py --apply --force
```

## Remaining Work: Phase 3 (Voice Embeddings)

~280 episodes have AGT mentioned in the description but couldn't be identified by text analysis alone (too many speakers, ambiguous word counts, two Antonios in the same episode). These require voice-based identification:

1. Build a voice fingerprint for AGT from confirmed episodes
2. Use a speaker verification model (e.g., pyannote embeddings, SpeechBrain) to match against LOCUTOR segments
3. Only assign when cosine similarity exceeds a high threshold

This requires GPU access and a few confirmed AGT audio segments as seeds. The 861 high-confidence identifications provide abundant seed material.

## Canonical Name

All identifications use exactly: **Antonio García-Trevijano**

Never abbreviated (not "Don Antonio", "AGT", "García-Trevijano", or "Trevijano"). This ensures consistent querying in the RAG system and uniform display on the website.

## Date

First run: 2026-03-31
