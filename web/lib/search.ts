/**
 * The job-board filter, ported from site/static/search.js with its behaviour intact.
 *
 * This is a re-skin, not a redesign: the same URLs have to return the same roles.
 * Every rule below was ported line by line from the original rather than rewritten
 * from a description of it, because several of them are deliberate and would look
 * like bugs to anyone reading only this file. They are called out where they sit.
 *
 * The payload is dictionary-encoded to cut the parsed heap on a phone. Decoding
 * happens once on load, so every filter compares plain strings - doing it per
 * comparison would move the cost into the keystroke path, where it is felt.
 */

/** A row as it arrives in jobs.json, before decoding. */
export interface RawRow {
  t: string; // job title
  c: number | null; // company        (dict index)
  s: number | null; // state          (dict index)
  n: string | null; // country, "us" | "ca"
  l: number | null; // seniority      (dict index)
  d: number; // days since posted
  w: number | null; // stated salary floor
  h: number | null; // link prefix    (index into prefixes)
  u: string | null; // link remainder
  f?: number; // fit score - carried in the payload, unused by search
  e: number | null; // eligibility    (dict index)
  p: number | null; // posting sponsorship (dict index)
  v?: number; // sponsor filings
  r?: 0 | 1; // remote
  k: number[]; // technology slugs (dict indices)
}

/** The same row after decode(): indices replaced by the strings they stand for. */
export interface Row extends Omit<RawRow, "c" | "s" | "l" | "e" | "p" | "k"> {
  c: string | null;
  s: string | null;
  l: string | null;
  e: string | null;
  p: string | null;
  k: string[];
}

export interface Payload {
  prefixes: string[];
  dicts: Record<string, string[]>;
  rows: RawRow[];
}

export interface Filters {
  q: string[];
  skills: string[];
  country: string;
  level: string;
  sponsor: string;
  salary: number;
  maxDays: number;
  paidOnly: boolean;
  states: string[];
}

/**
 * The sentinel that means "remote" in the state list.
 *
 * A NUL-prefixed string, because it shares a list with real state names and no
 * state can collide with it. Preserved verbatim: it is also written into the `where`
 * URL parameter, so changing it would break links people have already shared.
 */
export const REMOTE = "\u0000remote";

export const EMPTY_FILTERS: Filters = {
  q: [],
  skills: [],
  country: "",
  level: "",
  sponsor: "",
  salary: 0,
  maxDays: 0,
  paidOnly: false,
  states: [],
};

/** Expand dictionary indices back into strings. Once, on load. */
export function decode(payload: Payload): Row[] {
  const d = payload.dicts ?? {};
  const single = ["c", "s", "l", "e", "p"] as const;
  return (payload.rows ?? []).map((raw) => {
    const row = { ...raw } as unknown as Row;
    for (const field of single) {
      const table = d[field];
      const index = raw[field];
      row[field] = table && index != null ? (table[index] ?? null) : null;
    }
    row.k = d.k && raw.k ? raw.k.map((i) => d.k[i]).filter(Boolean) : [];
    return row;
  });
}

export function linkFor(row: Row, prefixes: string[]): string | null {
  if (row.u == null) return null;
  return row.h == null ? row.u : prefixes[row.h] + row.u;
}

/** The apply domain, shown on every row so a link is never a surprise. */
export function hostFor(link: string | null): string {
  if (!link) return "";
  try {
    return new URL(link).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/**
 * Does this role survive the current filters?
 *
 * Three rules here are intentional and each has cost somebody an afternoon:
 *
 *  - `q` is ALL-of across whitespace-separated terms, matched as substrings of
 *    "title company". `skills` is ANY-of. Requiring every skill returns nothing for
 *    most people, which reads as a broken search rather than a strict one.
 *  - Several states combine as OR. "California or New York" is the question people
 *    actually have; "both at once" is not a thing a single role can be.
 *  - `sponsor === "stated"` is satisfied ONLY by a posting that says so. An
 *    employer's filing history describes the employer, not this role, so it cannot
 *    satisfy a filter the visitor reads as "this job sponsors". Note that the
 *    summary line deliberately counts a different thing - see countSponsors.
 */
export function keep(r: Row, f: Filters): boolean {
  if (f.q.length) {
    const hay = `${r.t} ${r.c ?? ""}`.toLowerCase();
    for (const term of f.q) if (!hay.includes(term)) return false;
  }
  if (f.skills.length) {
    if (!f.skills.some((skill) => r.k.includes(skill))) return false;
  }
  if (f.country && r.n !== f.country) return false;
  if (f.states.length) {
    const hit = f.states.some((s) => (s === REMOTE ? r.r === 1 : r.s === s));
    if (!hit) return false;
  }
  if (f.level && r.l !== f.level) return false;
  if (f.maxDays && r.d > f.maxDays) return false;
  if (f.paidOnly && !r.w) return false;
  if (f.salary && !(r.w && r.w >= f.salary)) return false;
  if (f.sponsor === "stated" && r.p !== "offered_in_posting") return false;
  if (f.sponsor === "open" && (r.e === "blocked" || r.p === "no_sponsorship_this_role"))
    return false;
  return true;
}

/**
 * How many of these are at employers that sponsor.
 *
 * Deliberately NOT the same test as the "stated" filter above. The filter answers
 * "does this role say it sponsors"; this line answers "how many of these employers
 * have sponsored before", which is a useful and different thing, and conflating them
 * would overstate one of the two.
 */
export function countSponsors(rows: Row[]): number {
  return rows.filter((r) => r.p === "frequent_sponsor" || r.p === "has_sponsored")
    .length;
}

export function countInterns(rows: Row[]): number {
  return rows.filter((r) => r.l === "intern").length;
}

/** What the two text boxes mean once split. Mirrors filters() in search.js. */
export function parseQuery(text: string): string[] {
  return text.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

export function parseSkills(text: string): string[] {
  return text.trim().toLowerCase().split(/[,\s]+/).filter(Boolean);
}

/**
 * The URL parameters, which are public.
 *
 * These are in links people have shared and in search results. The names and the
 * encoding are part of the site's contract with the outside world, so they are
 * reproduced exactly - including `where` being pipe-joined and using the literal
 * "remote" for the sentinel.
 */
export function toSearchParams(f: Filters, queryText: string, skillsText: string): string {
  const p = new URLSearchParams();
  if (queryText.trim()) p.set("q", queryText.trim());
  if (skillsText.trim()) p.set("skills", skillsText.trim());
  if (f.country) p.set("country", f.country);
  if (f.level) p.set("level", f.level);
  if (f.sponsor) p.set("visa", f.sponsor);
  if (f.salary) p.set("salary", String(f.salary));
  if (f.maxDays) p.set("days", String(f.maxDays));
  if (f.paidOnly) p.set("paid", "1");
  if (f.states.length)
    p.set("where", f.states.map((s) => (s === REMOTE ? "remote" : s)).join("|"));
  return p.toString();
}

export function fromSearchParams(search: string): {
  filters: Filters;
  queryText: string;
  skillsText: string;
} {
  const p = new URLSearchParams(search);
  const queryText = p.get("q") ?? "";
  const skillsText = p.get("skills") ?? "";
  const where = p.get("where");
  return {
    queryText,
    skillsText,
    filters: {
      q: parseQuery(queryText),
      skills: parseSkills(skillsText),
      country: p.get("country") ?? "",
      level: p.get("level") ?? "",
      sponsor: p.get("visa") ?? "",
      salary: parseInt(p.get("salary") ?? "", 10) || 0,
      maxDays: parseInt(p.get("days") ?? "", 10) || 0,
      paidOnly: p.get("paid") === "1",
      states: where
        ? where.split("|").filter(Boolean).map((s) => (s === "remote" ? REMOTE : s))
        : [],
    },
  };
}

/**
 * The state dropdown is built from the data, not hard-coded.
 *
 * Scoped to the selected country, counted, and with already-picked states removed,
 * so the list never offers something that would return nothing.
 */
export function statesFor(
  rows: Row[],
  country: string,
  picked: string[],
): Array<{ value: string; label: string; count: number }> {
  const counts = new Map<string, number>();
  let remote = 0;
  for (const r of rows) {
    if (country && r.n !== country) continue;
    if (r.r === 1) remote += 1;
    if (!r.s) continue;
    counts.set(r.s, (counts.get(r.s) ?? 0) + 1);
  }
  const out = [...counts.entries()]
    .filter(([value]) => !picked.includes(value))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([value, count]) => ({ value, label: value, count }));
  if (remote && !picked.includes(REMOTE)) {
    out.unshift({ value: REMOTE, label: "Remote", count: remote });
  }
  return out;
}

/** The six preset chips, as they behave in search.js. */
export type PresetId = "intern" | "sponsor" | "de" | "fresh" | "paid" | "canada";

export const PRESETS: Array<{ id: PresetId; label: string }> = [
  { id: "intern", label: "Internships" },
  { id: "sponsor", label: "Sponsorship stated" },
  { id: "de", label: "Data engineering" },
  { id: "fresh", label: "Posted this week" },
  { id: "paid", label: "Salary published" },
  { id: "canada", label: "Canada" },
];
