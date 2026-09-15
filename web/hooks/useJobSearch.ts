"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  EMPTY_FILTERS,
  REMOTE,
  decode,
  fromSearchParams,
  keep,
  parseQuery,
  parseSkills,
  toSearchParams,
  type Filters,
  type Payload,
  type PresetId,
  type Row,
} from "@/lib/search";

/**
 * All of the job board's behaviour, kept out of the component.
 *
 * The page renders; this decides. That split matters here more than usual because
 * the rules are load-bearing and were ported rather than invented - keeping them in
 * one hook means the next person changing the layout cannot accidentally change what
 * the filters mean.
 *
 * Three timings are preserved from search.js because they are felt rather than seen:
 * 110ms debounce on the text boxes, 100 results then 200 more per click, and
 * immediate response on the selects.
 */
const DEBOUNCE_MS = 110;
const PAGE_ONE = 100;
const PAGE_MORE = 200;

export interface JobSearch {
  ready: boolean;
  failed: boolean;
  rows: Row[];
  results: Row[];
  shown: Row[];
  prefixes: string[];
  filters: Filters;
  queryText: string;
  skillsText: string;
  presets: Set<PresetId>;
  setQueryText: (v: string) => void;
  setSkillsText: (v: string) => void;
  setFilter: <K extends keyof Filters>(key: K, value: Filters[K]) => void;
  addState: (value: string) => void;
  removeState: (value: string) => void;
  togglePreset: (id: PresetId) => void;
  showMore: () => void;
  canShowMore: boolean;
}

export function useJobSearch(dataUrl: string): JobSearch {
  const [rows, setRows] = useState<Row[]>([]);
  const [prefixes, setPrefixes] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  const [queryText, setQueryTextRaw] = useState("");
  const [skillsText, setSkillsTextRaw] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [presets, setPresets] = useState<Set<PresetId>>(new Set());
  const [limit, setLimit] = useState(PAGE_ONE);

  // The URL is read once, before any fetch, so a shared link applies its filters to
  // the first render rather than flashing the unfiltered list first.
  const restored = useRef(false);
  if (!restored.current && typeof window !== "undefined") {
    restored.current = true;
    const state = fromSearchParams(window.location.search);
    if (window.location.search) {
      setFilters(state.filters);
      setQueryTextRaw(state.queryText);
      setSkillsTextRaw(state.skillsText);
    }
  }

  useEffect(() => {
    let live = true;
    fetch(dataUrl)
      .then((res) => {
        if (!res.ok) throw new Error(String(res.status));
        return res.json() as Promise<Payload>;
      })
      .then((payload) => {
        if (!live) return;
        setPrefixes(payload.prefixes ?? []);
        setRows(decode(payload));
        setReady(true);
      })
      .catch(() => {
        // The page says so rather than sitting on "Loading roles…" forever. The
        // rest of the site works without this file and the message says which
        // parts those are.
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [dataUrl]);

  // Debounced text. The parsed arrays are what the predicate sees, so a keystroke
  // costs a split and nothing else until the timer fires.
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);
  const setQueryText = useCallback((value: string) => {
    setQueryTextRaw(value);
    clearTimeout(debounce.current);
    debounce.current = setTimeout(
      () => setFilters((f) => ({ ...f, q: parseQuery(value) })),
      DEBOUNCE_MS,
    );
  }, []);

  const setSkillsText = useCallback((value: string) => {
    setSkillsTextRaw(value);
    clearTimeout(debounce.current);
    debounce.current = setTimeout(
      () => setFilters((f) => ({ ...f, skills: parseSkills(value) })),
      DEBOUNCE_MS,
    );
  }, []);

  const setFilter = useCallback(
    <K extends keyof Filters>(key: K, value: Filters[K]) =>
      setFilters((f) => ({ ...f, [key]: value })),
    [],
  );

  const addState = useCallback((value: string) => {
    if (!value) return;
    setFilters((f) =>
      f.states.includes(value) ? f : { ...f, states: [...f.states, value] },
    );
  }, []);

  const removeState = useCallback((value: string) => {
    setFilters((f) => ({ ...f, states: f.states.filter((s) => s !== value) }));
  }, []);

  /**
   * The six presets, each the exact pair of set/unset from search.js.
   *
   * They are toggles over the same filter state rather than a separate mode, so a
   * preset and a hand-set filter compose instead of fighting - pressing
   * "Internships" and then choosing Canada does what it looks like it does.
   */
  const togglePreset = useCallback((id: PresetId) => {
    setPresets((current) => {
      const next = new Set(current);
      const on = !next.has(id);
      if (on) next.add(id);
      else next.delete(id);

      setFilters((f) => {
        const out = { ...f };
        switch (id) {
          case "intern":
            out.level = on ? "intern" : "";
            break;
          case "sponsor":
            out.sponsor = on ? "stated" : "";
            break;
          case "de":
            // The same three slugs the original preset used.
            out.skills = on ? ["airflow", "dbt", "spark"] : [];
            break;
          case "fresh":
            out.maxDays = on ? 7 : 0;
            break;
          case "paid":
            out.paidOnly = on;
            break;
          case "canada":
            out.country = on ? "ca" : "";
            break;
        }
        return out;
      });
      return next;
    });
  }, []);

  // "de" writes into the skills box, so the box has to show it. Kept in sync here
  // rather than in the component, where it would be easy to forget on one path.
  useEffect(() => {
    if (presets.has("de")) setSkillsTextRaw("airflow, dbt, spark");
    else if (skillsText === "airflow, dbt, spark") setSkillsTextRaw("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets]);

  const results = useMemo(
    () => (ready ? rows.filter((r) => keep(r, filters)) : []),
    [ready, rows, filters],
  );

  useEffect(() => setLimit(PAGE_ONE), [filters]);

  // The URL mirrors the filters so any view can be shared. replaceState, not push:
  // typing three letters should not put three entries in the back button.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const qs = toSearchParams(filters, queryText, skillsText);
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    window.history.replaceState(null, "", next);
  }, [filters, queryText, skillsText]);

  return {
    ready,
    failed,
    rows,
    results,
    shown: results.slice(0, limit),
    prefixes,
    filters,
    queryText,
    skillsText,
    presets,
    setQueryText,
    setSkillsText,
    setFilter,
    addState,
    removeState,
    togglePreset,
    showMore: () => setLimit((n) => n + PAGE_MORE),
    canShowMore: results.length > limit,
  };
}

export { REMOTE };
