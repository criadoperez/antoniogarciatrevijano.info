import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

// ── Types ──────────────────────────────────────────────────────────────────

export interface AudioEntry {
  title: string;
  date: string;
  uploader: string;
  ivoox_url: string;
  ivoox_id: string;
  duration_seconds: number;
  audio_filename: string;
  audio_cid: string;
  speakers: string[];
  body: string;
}

export interface SrtSegment {
  start: number; // seconds
  end: number;   // seconds
  speaker: string;
  text: string;
}

export interface SrtTurn {
  speaker: string;
  segments: SrtSegment[];
}

// ── .md parsing ────────────────────────────────────────────────────────────

function getString(fm: string, key: string): string {
  const m = fm.match(new RegExp(`^${key}:\\s*"([^"]*)"`, "m"));
  return m ? m[1] : "";
}

function getInt(fm: string, key: string): number {
  const m = fm.match(new RegExp(`^${key}:\\s*(\\d+)`, "m"));
  return m ? parseInt(m[1], 10) : 0;
}

function getSpeakers(fm: string): string[] {
  const m = fm.match(/^speakers:\s*(\[.*\])/m);
  if (!m) return [];
  try {
    return JSON.parse(m[1]) as string[];
  } catch {
    return [];
  }
}

export function parseAudioMd(content: string): AudioEntry | null {
  const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return null;
  const fm = match[1];
  const ivoox_id = getString(fm, "ivoox_id");
  if (!ivoox_id) return null;
  return {
    title: getString(fm, "title"),
    date: getString(fm, "date"),
    uploader: getString(fm, "uploader"),
    ivoox_url: getString(fm, "ivoox_url"),
    ivoox_id,
    duration_seconds: getInt(fm, "duration_seconds"),
    audio_filename: getString(fm, "audio_filename"),
    audio_cid: getString(fm, "audio_cid"),
    speakers: getSpeakers(fm),
    body: match[2],
  };
}

export function loadAllAudios(audiosDir: string): AudioEntry[] {
  return readdirSync(audiosDir)
    .filter((f) => f.endsWith(".md"))
    .map((filename) =>
      parseAudioMd(readFileSync(join(audiosDir, filename), "utf-8"))
    )
    .filter((e): e is AudioEntry => e !== null)
    .sort((a, b) => a.date.localeCompare(b.date));
}

// ── .srt parsing ───────────────────────────────────────────────────────────

function srtTimeToSeconds(time: string): number {
  // Format: HH:MM:SS,mmm
  const [hms, ms] = time.split(",");
  const [h, m, s] = hms.split(":").map(Number);
  return h * 3600 + m * 60 + s + Number(ms) / 1000;
}

export function parseSrt(content: string): SrtSegment[] {
  const segments: SrtSegment[] = [];
  // Each SRT block is separated by a blank line
  const blocks = content.trim().split(/\n\n+/);
  for (const block of blocks) {
    const lines = block.trim().split("\n");
    // Need at least: index, timestamp, text
    if (lines.length < 3) continue;
    // lines[0] = index number (ignored)
    // lines[1] = "HH:MM:SS,mmm --> HH:MM:SS,mmm"
    // lines[2..] = text (always one line in our files)
    const timeMatch = lines[1].match(
      /(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})/
    );
    if (!timeMatch) continue;
    const start = srtTimeToSeconds(timeMatch[1]);
    const end = srtTimeToSeconds(timeMatch[2]);
    // Text line format: "[SPEAKER_LABEL] sentence text"
    const rawText = lines.slice(2).join(" ");
    const speakerMatch = rawText.match(/^\[([^\]]+)\]\s*(.*)/);
    if (!speakerMatch) continue;
    const text = speakerMatch[2].trim();
    if (!text) continue;
    segments.push({ start, end, speaker: speakerMatch[1], text });
  }
  return segments;
}

export function loadSrt(audiosDir: string, ivoox_id: string): SrtSegment[] {
  const srtPath = join(audiosDir, `${ivoox_id}.srt`);
  if (!existsSync(srtPath)) return [];
  return parseSrt(readFileSync(srtPath, "utf-8"));
}

export function groupIntoTurns(segments: SrtSegment[]): SrtTurn[] {
  const turns: SrtTurn[] = [];
  for (const seg of segments) {
    const last = turns[turns.length - 1];
    if (last && last.speaker === seg.speaker) {
      last.segments.push(seg);
    } else {
      turns.push({ speaker: seg.speaker, segments: [seg] });
    }
  }
  return turns;
}

// ── Formatting helpers ─────────────────────────────────────────────────────

export function formatDuration(seconds: number): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m.toString().padStart(2, "0")}m`;
  return `${m}m`;
}

export function formatSpeaker(label: string): string {
  // "LOCUTOR_00" → "Locutor 00", "DESCONOCIDO" → "Desconocido"
  return (
    label.charAt(0).toUpperCase() +
    label.slice(1).toLowerCase().replace(/_/g, " ")
  );
}
