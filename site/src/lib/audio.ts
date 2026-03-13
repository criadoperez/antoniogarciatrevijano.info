import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

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

export function formatDuration(seconds: number): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m.toString().padStart(2, "0")}m`;
  return `${m}m`;
}
