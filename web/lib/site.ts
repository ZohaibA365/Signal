/**
 * Everything the generated pages read, and the two judgements they make.
 *
 * Build-time only: company_detail.json is 10MB and none of this may be imported
 * from a "use client" module.
 *
 * The judgements - which headline to show, and how a company compares to its peers -
 * are ported from site/build.py rather than reinvented. Both are deliberately
 * conservative and the reasons are recorded where they sit, because both have
 * already produced a false claim once.
 */
import fs from "node:fs";
import path from "node:path";

const DIR = path.join(process.cwd(), "data");

function read<T>(name: string): T {
  return JSON.parse(fs.readFileSync(path.join(DIR, `${name}.json`), "utf8")) as T;
}

/** The same rule as build.py's slugify. The URLs it produces are already indexed. */
export function slugify(name: string): string {
  const s = (name ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s || "unknown";
}

export interface Company {
  company_name: string;
  total_postings: number;
  postings_last_30d: number;
  postings_prior_30d: number;
  pace_change_pct: number | null;
  distinct_states: number | null;
  distinct_countries: number | null;
  distinct_departments: number | null;
  intern_postings: number | null;
  latest_posting: string | null;
  sponsorship_status: string | null;
  total_filings: number | null;
  certified_pct: number | null;
  weighted_median_wage: number | null;
  years_filing: number | null;
  match_type: string | null;
}

export interface CompanyTech {
  company_name: string;
  tech_slug: string;
  tech_name: string;
  category: string;
  mentions: number;
}

export interface CompanyRole {
  company_name: string;
  job_title: string;
  location_state: string | null;
  country: string | null;
  seniority: string | null;
  days_since_posted: number;
  redirect_url: string | null;
}

export interface CompanyPeer {
  company_name: string;
  peer_name: string;
  peer_rank: number;
  shared_technologies: number;
  similarity: number;
  total_postings: number;
  postings_last_30d: number;
  distinct_states: number | null;
  pace_change_pct: number | null;
  total_filings: number | null;
}

export interface MarketPosition {
  company_name: string;
  tech_slug: string;
  tech_name: string;
  category: string;
  mentions: number;
  openings: number | null;
  category_rank: number | null;
  pct_of_category: number | null;
  pct_top_band: number | null;
}

export interface Tech {
  tech_slug: string;
  tech_name: string;
  category: string;
  postings_mentioning: number;
  is_tracked: boolean;
  is_ubiquitous: boolean;
  openings: number | null;
  overall_rank: number | null;
  category_rank: number | null;
  pct_of_category: number | null;
  pct_top_band: number | null;
  pct_under_80k: number | null;
  salary_sample: number | null;
}

export interface TechEmployer {
  tech_slug: string;
  company_name: string;
  postings: number;
  rank: number;
}

export interface DemandRow {
  category: string;
  tech_slug: string;
  tech_name: string;
  openings: number;
  pct_of_category: number | null;
  category_rank: number;
}

export interface SalaryRow {
  tech_slug: string;
  tech_name: string;
  category: string;
  pct_top_band: number | null;
  total_postings: number;
}

export interface StackPair {
  tech_slug: string;
  tech_name: string;
  co_tech_slug: string;
  co_tech_name: string;
  pct_of_tech_postings: number | null;
  lift: number | null;
  co_postings: number;
}

export interface Pace {
  company_name: string;
  postings_observed: number;
  first_observed: string;
  last_observed: string;
  distinct_open_days: number;
  median_days_open: number | null;
  closed_share: number | null;
}

export interface Coverage {
  measured_days: number;
  panel_days: number;
  first_measured: string;
  last_measured: string;
  min_measured_days_required: number;
  trend_is_publishable: boolean;
}

// --- loaders, each read once per build ---------------------------------------

let _companies: Company[] | null = null;
export function companies(): Company[] {
  _companies ??= read<{ COMPANIES: Company[] }>("companies").COMPANIES;
  return _companies;
}

type Detail = {
  COMPANY_TECH: CompanyTech[];
  COMPANY_ROLES: CompanyRole[];
  COMPANY_PEERS: CompanyPeer[];
  COMPANY_MARKET_POSITION: MarketPosition[];
};
let _detail: Detail | null = null;
export function detail(): Detail {
  _detail ??= read<Detail>("company_detail");
  return _detail;
}

type TechData = { TECH_DETAIL: Tech[]; TECH_EMPLOYERS: TechEmployer[] };
let _tech: TechData | null = null;
export function techData(): TechData {
  _tech ??= read<TechData>("tech");
  return _tech;
}

type MarketData = {
  DEMAND_BY_CATEGORY: DemandRow[];
  SALARY_LEADERS: SalaryRow[];
  STACK_PAIRS: StackPair[];
};
let _market: MarketData | null = null;
export function marketData(): MarketData {
  _market ??= read<MarketData>("market");
  return _market;
}

type HistoryData = {
  HISTORY_COVERAGE: Coverage[];
  COMPANY_PACE: Pace[];
  TECH_HISTORY: Array<{ observed_date: string; tech_slug: string; postings_mentioning: number }>;
};
let _history: HistoryData | null = null;
export function history(): HistoryData {
  _history ??= read<HistoryData>("history");
  return _history;
}

export function retired(): Array<{ company_name: string; canonical_name: string }> {
  return read<{ RETIRED_COMPANIES: Array<{ company_name: string; canonical_name: string }> }>(
    "retired",
  ).RETIRED_COMPANIES;
}

/** Group any of the detail tables by company, once. */
export function groupBy<T>(rows: T[], key: (row: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const row of rows) {
    const k = key(row);
    const list = out.get(k);
    if (list) list.push(row);
    else out.set(k, [row]);
  }
  return out;
}

/**
 * Can a month-over-month claim be made at all?
 *
 * Deliberately stricter than the site's own chart threshold. A misleading chart is
 * bad; a false claim in front of the employer it is about is unrecoverable - they
 * will conclude the whole project is unreliable, and they would be right to.
 */
export const MIN_DAYS_FOR_TREND = 45;

export function trendOk(): boolean {
  const c = history().HISTORY_COVERAGE[0];
  const days = c?.measured_days ?? 0;
  return days >= MIN_DAYS_FOR_TREND;
}

export interface PeerSummary {
  count: number;
  median_last_30d: number;
  own_last_30d: number;
  ratio: number | null;
  names: string;
}

/**
 * How a company compares to its peers on hiring pace, or nothing.
 *
 * Returns null when there are no peers, and the page then shows no comparison at
 * all. A wrong peer claim on a page the company itself may read is worse than a
 * missing section.
 */
export function peerSummary(peers: CompanyPeer[], c: Company): PeerSummary | null {
  if (!peers.length) return null;
  const last30 = peers.map((p) => p.postings_last_30d ?? 0).sort((a, b) => a - b);
  const median = last30[Math.floor(last30.length / 2)];
  const own = c.postings_last_30d ?? 0;
  const ratio = median ? own / median : null;
  return {
    count: peers.length,
    median_last_30d: median,
    own_last_30d: own,
    ratio: ratio ? Math.round(ratio * 10) / 10 : null,
    names: peers.slice(0, 3).map((p) => p.peer_name).join(", "),
  };
}

/**
 * The single strongest true observation about a company, or nothing.
 *
 * NO MONTH-OVER-MONTH CLAIM until the corpus can support one. An earlier version of
 * this produced "Google's hiring is up 2600%", which is false: boards delist filled
 * roles, so a freshly collected corpus always shows far more recent postings than
 * older ones. With a handful of collection days behind it, a pace claim measures
 * when collection started, not how a company is hiring.
 *
 * Counts, shares and peer-relative comparisons survive that bias because it lands on
 * both sides and mostly cancels. Absolute change over time does not.
 */
export function headlineFor(c: Company, ok: boolean): string | null {
  const total = c.total_postings ?? 0;
  const last30 = c.postings_last_30d ?? 0;

  if (ok && (c.postings_prior_30d ?? 0) >= 10) {
    const prior = c.postings_prior_30d;
    const change = Math.round((100 * (last30 - prior)) / prior);
    if (Math.abs(change) >= 50) {
      const direction = change > 0 ? "accelerated" : "slowed";
      return (
        `Hiring has ${direction}: ${last30} roles posted in the last 30 days, ` +
        `${change > 0 ? "up" : "down"} ${Math.abs(change)}% on the month before.`
      );
    }
  }

  if (c.total_filings && c.total_filings >= 20) {
    return (
      `Has filed ${Math.round(c.total_filings).toLocaleString("en-US")} US visa ` +
      `applications, so sponsorship is a matter of record rather than inference.`
    );
  }
  if ((c.distinct_states ?? 0) >= 15) return `Hiring across ${c.distinct_states} states.`;
  if (total >= 40)
    return `${total.toLocaleString("en-US")} tracked roles, ${last30} of them posted in the last 30 days.`;
  return null;
}

/** Relative bar widths, as build.py's with_bars does. */
export function withBars<T>(rows: T[], value: (r: T) => number): Array<T & { bar: number }> {
  const max = Math.max(...rows.map(value), 0);
  return rows.map((r) => ({ ...r, bar: max ? Math.round((100 * value(r)) / max) : 0 }));
}
