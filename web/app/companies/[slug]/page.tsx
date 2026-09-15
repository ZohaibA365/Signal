import Link from "next/link";
import { notFound } from "next/navigation";
import { KpiTile } from "@/components/ui/Card";
import { Meter } from "@/components/ui/Meter";
import { Kpis, Note, Page, PageHeader, Section } from "@/components/ui/Page";
import { Table, Td, Th } from "@/components/ui/Table";
import { money, num } from "@/lib/format";
import {
  companies,
  detail,
  groupBy,
  headlineFor,
  history,
  peerSummary,
  slugify,
  trendOk,
  withBars,
  type Company,
} from "@/lib/site";

/**
 * One employer. 1,118 of these are generated at build time.
 *
 * The page is assembled from the same sections the Jinja template had, in the same
 * order, showing the same figures - including the ones that are deliberately absent.
 * Several sections simply do not render when the evidence is thin, and that is the
 * point rather than an oversight: a peer comparison with no peers, or a sponsorship
 * claim with no matched filings, would be a false statement on a page the employer
 * themselves may read.
 */
export function generateStaticParams() {
  return companies().map((c) => ({ slug: slugify(c.company_name) }));
}

function findCompany(slug: string): Company | undefined {
  return companies().find((c) => slugify(c.company_name) === slug);
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const c = findCompany(slug);
  if (!c) return {};
  return {
    title: `${c.company_name} — hiring and sponsorship`,
    description:
      `${num(c.total_postings)} tracked roles at ${c.company_name}, ` +
      `${num(c.postings_last_30d)} posted in the last 30 days.`,
  };
}

export default async function CompanyPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const c = findCompany(slug);
  if (!c) notFound();

  const d = detail();
  const techs = withBars(
    (groupBy(d.COMPANY_TECH, (r) => r.company_name).get(c.company_name) ?? [])
      .slice()
      .sort((a, b) => b.mentions - a.mentions)
      .slice(0, 10),
    (t) => t.mentions,
  );
  const roles = (groupBy(d.COMPANY_ROLES, (r) => r.company_name).get(c.company_name) ?? [])
    .slice(0, 25);
  const peers = groupBy(d.COMPANY_PEERS, (r) => r.company_name).get(c.company_name) ?? [];
  const marketPos = (
    groupBy(d.COMPANY_MARKET_POSITION, (r) => r.company_name).get(c.company_name) ?? []
  )
    .filter((m) => m.mentions >= 2)
    .slice(0, 8);
  const pace = history().COMPANY_PACE.find((p) => p.company_name === c.company_name);
  const summary = peerSummary(peers, c);
  const headline = headlineFor(c, trendOk());

  return (
    <Page>
      <PageHeader
        title={c.company_name}
        sub={
          <>
            {num(c.total_postings)} tracked roles · {num(c.postings_last_30d)} in the last
            30 days
            {c.distinct_states && c.distinct_states > 1 ? ` · ${c.distinct_states} states` : ""}
          </>
        }
        lead={headline}
      />

      <Kpis>
        <KpiTile label="Tracked roles" value={num(c.total_postings)} />
        <KpiTile label="Last 30 days" value={num(c.postings_last_30d)} />
        {c.total_filings ? (
          <KpiTile label="Visa filings" value={num(c.total_filings)} />
        ) : null}
      </Kpis>

      {summary && (
        <Section title="How they compare">
          <p className="max-w-prose text-body text-text-2">
            Against {summary.count} companies hiring for a similar technology mix (
            {summary.names}
            {summary.count > 3 ? " and others" : ""}), {c.company_name} posted{" "}
            <b className="font-mono text-data text-text">{summary.own_last_30d}</b> roles in
            the last 30 days against a peer median of{" "}
            <b className="font-mono text-data text-text">{summary.median_last_30d}</b>
            {summary.ratio ? ` — ${summary.ratio}× the pace` : ""}.
          </p>
          <div className="mt-3">
            <Table>
              <thead>
                <tr>
                  <Th>Company</Th>
                  <Th numeric>Roles</Th>
                  <Th numeric>Last 30d</Th>
                  <Th numeric>States</Th>
                  <Th numeric>Visa filings</Th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <Td>
                    <b className="text-text">{c.company_name}</b>
                  </Td>
                  <Td numeric>{num(c.total_postings)}</Td>
                  <Td numeric>{num(c.postings_last_30d)}</Td>
                  <Td numeric>{c.distinct_states ?? "—"}</Td>
                  <Td numeric>{c.total_filings ? num(c.total_filings) : "—"}</Td>
                </tr>
                {peers.map((p) => (
                  <tr key={p.peer_name}>
                    <Td>
                      <Link
                        href={`/companies/${slugify(p.peer_name)}/`}
                        className="text-text-2 transition-base hover:text-accent"
                      >
                        {p.peer_name}
                      </Link>
                    </Td>
                    <Td numeric>{num(p.total_postings)}</Td>
                    <Td numeric>{num(p.postings_last_30d)}</Td>
                    <Td numeric>{p.distinct_states ?? "—"}</Td>
                    <Td numeric>{p.total_filings ? num(p.total_filings) : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
          <Note>
            Peers are derived from the data, not assigned by hand: the companies whose
            postings mention the most similar set of technologies. Shown only where the
            overlap is large enough to be meaningful, which is why many companies have no
            comparison here.
          </Note>
        </Section>
      )}

      {pace?.median_days_open ? (
        <Section title="How long their roles stay open">
          <p className="max-w-prose text-body text-text-2">
            Of <b className="font-mono text-data text-text">{num(pace.postings_observed)}</b>{" "}
            roles observed on {c.company_name}&rsquo;s board, the typical one that has since
            come down was open for{" "}
            <b className="font-mono text-data text-text">
              {Math.round(pace.median_days_open)}
            </b>{" "}
            days.
            {pace.closed_share
              ? ` That median rests on the ${Math.round(pace.closed_share * 100)}% of the sample that has actually closed.`
              : ""}
          </p>
          <Note>
            Measured by checking each board daily and recording which postings are still
            listed, not inferred from posted dates. Only roles that have stopped appearing
            count towards the median: one still open has no end date yet, and one that was
            already open before this started looks younger than it is. Both would pull the
            figure down.
          </Note>
        </Section>
      ) : null}

      {marketPos.length > 0 && (
        <Section title="Their stack against the market">
          <Table>
            <thead>
              <tr>
                <Th>Technology</Th>
                <Th numeric>Their postings</Th>
                <Th numeric>US openings</Th>
                <Th numeric>Rank in category</Th>
              </tr>
            </thead>
            <tbody>
              {marketPos.map((m) => (
                <tr key={m.tech_slug}>
                  <Td>
                    <Link
                      href={`/tech/${m.tech_slug}/`}
                      className="text-text-2 transition-base hover:text-accent"
                    >
                      {m.tech_name}
                    </Link>
                  </Td>
                  <Td numeric>{num(m.mentions)}</Td>
                  <Td numeric>{num(m.openings)}</Td>
                  <Td numeric>
                    #{m.category_rank} in {m.category}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Note>
            Market openings come from the market-wide index, updated daily, so they do not
            depend on which postings Signal collected. Ubiquitous tools such as Python and
            SQL are excluded because naming them says nothing about an employer.
          </Note>
        </Section>
      )}

      <Section title="Visa sponsorship">
        {c.total_filings ? (
          <>
            <p className="max-w-prose text-body text-text-2">
              Filed <b className="font-mono text-data text-text">{num(c.total_filings)}</b>{" "}
              labour condition applications with the US Department of Labor
              {c.years_filing
                ? ` across ${c.years_filing} fiscal year${c.years_filing > 1 ? "s" : ""}`
                : ""}
              {c.certified_pct ? `, ${c.certified_pct.toFixed(1)}% approved` : ""}
              {/* Printed as given, not rounded. The Jinja page shows 99.9%, and
                  rounding that to 100% overstates an approval rate - the one
                  direction this project does not round in. */}
              {c.weighted_median_wage
                ? `, at a median attested wage of ${money(c.weighted_median_wage)}`
                : ""}
              .
            </p>
            <Note>
              These are filings, not inferences — the employer told the government what it
              intended to pay. Filing history shows the company <b>has</b> sponsored, not
              that it will for any particular role.
            </Note>
          </>
        ) : (
          <>
            <p className="text-body text-text-2">
              No Department of Labor filings matched this employer.
            </p>
            <Note>
              Absence of a match is weak evidence: employers file under legal entity names
              that do not always resolve to the name on a job posting, and only matches
              confident enough to state as fact are shown.
            </Note>
          </>
        )}
      </Section>

      {techs.length > 0 && (
        <Section title="What their postings ask for">
          <Table>
            <thead>
              <tr>
                <Th>Technology</Th>
                <Th>Share of their postings</Th>
                <Th numeric>Postings</Th>
              </tr>
            </thead>
            <tbody>
              {techs.map((t, i) => (
                <tr key={t.tech_slug}>
                  <Td>
                    <Link
                      href={`/tech/${t.tech_slug}/`}
                      className="text-text-2 transition-base hover:text-accent"
                    >
                      {t.tech_name}
                    </Link>
                  </Td>
                  <Td className="w-[46%]">
                    <Meter value={t.bar} dim={i > 2} />
                  </Td>
                  <Td numeric>{num(t.mentions)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Section>
      )}

      {roles.length > 0 && (
        <Section title="Open roles">
          <Table>
            <thead>
              <tr>
                <Th>Role</Th>
                <Th>Location</Th>
                <Th>Level</Th>
                <Th numeric>Posted</Th>
              </tr>
            </thead>
            <tbody>
              {roles.map((r, i) => (
                <tr key={`${r.job_title}-${i}`}>
                  <Td>
                    {r.redirect_url ? (
                      <a
                        href={r.redirect_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-text transition-base hover:text-accent"
                      >
                        {r.job_title}
                      </a>
                    ) : (
                      r.job_title
                    )}
                  </Td>
                  <Td>{r.location_state ?? (r.country ?? "us").toUpperCase()}</Td>
                  <Td>{r.seniority}</Td>
                  <Td numeric>{r.days_since_posted}d</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Section>
      )}

      <p className="mt-7 text-small">
        <Link href="/companies/" className="text-text-3 transition-base hover:text-accent">
          ← All companies
        </Link>
      </p>
    </Page>
  );
}
