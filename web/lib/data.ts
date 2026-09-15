/**
 * Reading the exported warehouse data at BUILD time.
 *
 * These imports run in the Node process that renders the pages, never in a browser:
 * company_detail.json alone is 10MB and shipping it to a phone would undo the
 * entire reason the search payload is dictionary-encoded. Next tree-shakes what a
 * client component does not touch, but the honest guard is that nothing here is
 * imported from a "use client" module.
 */
import fs from "node:fs";
import path from "node:path";

const DIR = path.join(process.cwd(), "data");

function read<T>(name: string): T {
  return JSON.parse(fs.readFileSync(path.join(DIR, `${name}.json`), "utf8")) as T;
}

export interface CorpusStats {
  postings: number;
  companies: number;
  technologies: number;
  verified_sponsors: number;
  total_filings: number;
  tech_mentions: number;
}

export interface Freshness {
  latest_snapshot: string;
  latest_posting: string;
  days_of_history: number;
}

export interface Meta {
  generated_at: string;
  site_url: string;
  repo_url: string;
}

export function stats(): { corpus: CorpusStats; freshness: Freshness } {
  const d = read<{ CORPUS_STATS: CorpusStats[]; FRESHNESS: Freshness[] }>("stats");
  return { corpus: d.CORPUS_STATS[0], freshness: d.FRESHNESS[0] };
}

export function meta(): Meta {
  return read<Meta>("meta");
}

/**
 * How many roles the search set actually holds, and the hash that pairs the page
 * with its payload.
 *
 * Both come from the payload itself rather than being recomputed, so the number the
 * page states and the number of rows it downloads cannot disagree - the 20,000-row
 * cap is surfaced to the visitor and has to be the real figure.
 */
export function searchMeta(): { rows: number; hash: string } {
  const file = path.join(process.cwd(), "public", "data", "jobs.json");
  const raw = fs.readFileSync(file);
  const parsed = JSON.parse(raw.toString("utf8")) as { rows: unknown[] };
  const crypto = require("node:crypto") as typeof import("node:crypto");
  return {
    rows: parsed.rows.length,
    hash: crypto.createHash("sha256").update(raw).digest("hex").slice(0, 12),
  };
}
