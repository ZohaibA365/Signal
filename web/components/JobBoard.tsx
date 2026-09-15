"use client";

import { useMemo } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Input, Select } from "@/components/ui/Field";
import { useJobSearch } from "@/hooks/useJobSearch";
import { num, money, age } from "@/lib/format";
import {
  PRESETS,
  REMOTE,
  countInterns,
  countSponsors,
  hostFor,
  linkFor,
  statesFor,
  type Row,
} from "@/lib/search";

/**
 * The job board. Presentation only - every rule lives in useJobSearch.
 */
export function JobBoard({ dataUrl }: { dataUrl: string }) {
  const s = useJobSearch(dataUrl);

  const states = useMemo(
    () => (s.ready ? statesFor(s.rows, s.filters.country, s.filters.states) : []),
    [s.ready, s.rows, s.filters.country, s.filters.states],
  );

  return (
    <>
      <section className="border-b border-line bg-surface">
        <div className="mx-auto max-w-page px-4 py-7 sm:px-5">
          <h1 className="font-display text-display-2 font-medium text-text sm:text-display-1">
            Data &amp; AI jobs
          </h1>
          <p className="mt-2 max-w-prose text-body text-text-2">
            United States and Canada, updated daily. Every role links straight to the
            employer&rsquo;s own careers page.
          </p>

          {/* Seven controls, the same seven the site has today. A grid that reflows
              to one column on a phone rather than a row that scrolls. */}
          <div className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-filters">
            <Input
              type="search"
              value={s.queryText}
              onChange={(e) => s.setQueryText(e.target.value)}
              placeholder="Search role or company"
              aria-label="Search role or company"
              autoComplete="off"
              className="sm:col-span-2 lg:col-span-2"
            />
            <Input
              type="search"
              value={s.skillsText}
              onChange={(e) => s.setSkillsText(e.target.value)}
              placeholder="Skills: python, dbt, spark"
              aria-label="Skills"
              autoComplete="off"
            />
            <Select
              value={s.filters.country}
              onChange={(e) => s.setFilter("country", e.target.value)}
              aria-label="Country"
            >
              <option value="">Anywhere</option>
              <option value="us">United States</option>
              <option value="ca">Canada</option>
            </Select>
            <Select
              value={s.filters.level}
              onChange={(e) => s.setFilter("level", e.target.value)}
              aria-label="Level"
            >
              <option value="">Any level</option>
              <option value="intern">Intern</option>
              <option value="entry">Entry</option>
              <option value="mid">Mid</option>
              <option value="senior">Senior</option>
            </Select>
            <Select
              value={s.filters.sponsor}
              onChange={(e) => s.setFilter("sponsor", e.target.value)}
              aria-label="Visa"
            >
              <option value="">Any visa status</option>
              <option value="stated">Sponsors (stated in posting)</option>
              <option value="open">Hide roles that refuse sponsorship</option>
            </Select>
            <Select
              value=""
              onChange={(e) => s.addState(e.target.value)}
              aria-label="State or province"
            >
              <option value="">Any location</option>
              {states.map((st) => (
                <option key={st.value} value={st.value}>
                  {st.label} ({num(st.count)})
                </option>
              ))}
            </Select>
            <Input
              type="number"
              min={0}
              step={10000}
              value={s.filters.salary || ""}
              onChange={(e) =>
                s.setFilter("salary", parseInt(e.target.value, 10) || 0)
              }
              placeholder="Min salary"
              aria-label="Minimum stated salary"
            />
          </div>

          {/* Chosen locations. Several combine as OR, which is the question people
              actually have. */}
          {s.filters.states.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {s.filters.states.map((value) => (
                <Chip key={value} removable onClick={() => s.removeState(value)}>
                  {value === REMOTE ? "Remote" : value}
                </Chip>
              ))}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {PRESETS.map((p) => (
              <Chip
                key={p.id}
                pressed={s.presets.has(p.id)}
                onClick={() => s.togglePreset(p.id)}
              >
                {p.label}
              </Chip>
            ))}
          </div>

          <p className="tabular mt-4 font-mono text-data text-text-2" aria-live="polite">
            <Summary search={s} />
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-page px-4 py-6 sm:px-5">
        <ul className="divide-y divide-line border-y border-line">
          {s.shown.map((row, i) => (
            <JobRow key={`${row.t}-${row.c}-${i}`} row={row} prefixes={s.prefixes} />
          ))}
        </ul>

        {s.ready && s.results.length === 0 && (
          <p className="py-8 text-center text-body text-text-2">
            No roles match those filters.
          </p>
        )}

        {s.canShowMore && (
          <div className="mt-5 flex justify-center">
            <Button onClick={s.showMore}>
              Show more ({num(s.results.length - s.shown.length)} left)
            </Button>
          </div>
        )}

        <div className="mt-8 grid max-w-prose gap-4 text-small text-text-3">
          <p>
            Roles that could only be linked through an aggregator are not listed: those
            links are restricted by country and break for anyone outside the
            posting&rsquo;s own. They are counted in the market index but never shown
            as something to click.
          </p>
          <p>
            Salary shows only where the employer published it — roughly 99% of figures
            in the source are estimates and are excluded. Visa labels come from
            Department of Labor filings, not from job text.
          </p>
        </div>
      </div>
    </>
  );
}

function Summary({ search }: { search: ReturnType<typeof useJobSearch> }) {
  if (search.failed)
    return <>Could not load the role list. Company profiles and the market index still work.</>;
  if (!search.ready) return <>Loading roles…</>;

  const interns = countInterns(search.results);
  const sponsors = countSponsors(search.results);
  return (
    <>
      {num(search.results.length)} roles
      {interns > 0 && <> · {num(interns)} internships</>}
      {sponsors > 0 && <> · {num(sponsors)} at employers that sponsor</>}
    </>
  );
}

/**
 * One role.
 *
 * Level and work authorisation are two independent slots. They shared one once, and
 * an internship that required citizenship showed "US citizens only" while the word
 * "internship" vanished from a search for internships.
 */
function JobRow({ row, prefixes }: { row: Row; prefixes: string[] }) {
  const link = linkFor(row, prefixes);
  const host = hostFor(link);

  return (
    <li className="group py-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-4">
        <div className="min-w-0 flex-1">
          <a
            href={link ?? undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="text-body font-medium text-text transition-base hover:text-accent"
          >
            {row.t}
          </a>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-small text-text-2">
            <span>{row.c}</span>
            {row.s && <span className="text-text-3">· {row.s}</span>}
            {row.r === 1 && <span className="text-text-3">· Remote</span>}
            {host && <span className="text-text-3">· {host}</span>}
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <LevelTag row={row} />
          <AuthTag row={row} />
          {row.w ? (
            <span className="tabular font-mono text-data text-text">{money(row.w)}</span>
          ) : null}
          <span className="tabular w-10 text-right font-mono text-data text-text-3">
            {age(row.d)}
          </span>
        </div>
      </div>
    </li>
  );
}

function LevelTag({ row }: { row: Row }) {
  if (row.l === "intern") return <Badge tone="level">Internship</Badge>;
  if (row.l === "entry") return <Badge tone="level">Entry level</Badge>;
  if (row.l) return <Badge tone="neutral">{row.l}</Badge>;
  return null;
}

function AuthTag({ row }: { row: Row }) {
  if (row.e === "blocked") return <Badge tone="no">US citizens only</Badge>;
  if (row.p === "no_sponsorship_this_role") return <Badge tone="no">Does not sponsor</Badge>;
  if (row.p === "offered_in_posting") return <Badge tone="ok">Sponsors</Badge>;
  return null;
}
