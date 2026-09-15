import Link from "next/link";
import { notFound } from "next/navigation";
import { KpiTile } from "@/components/ui/Card";
import { Kpis, Note, Page, PageHeader, Section } from "@/components/ui/Page";
import { Sparkline } from "@/components/Sparkline";
import { Table, Td, Th } from "@/components/ui/Table";
import { num, pct } from "@/lib/format";
import { groupBy, history, marketData, slugify, techData, type Tech } from "@/lib/site";

/** One technology. 120 of these. */
export function generateStaticParams() {
  return techData().TECH_DETAIL.map((t) => ({ slug: t.tech_slug }));
}

function findTech(slug: string): Tech | undefined {
  return techData().TECH_DETAIL.find((t) => t.tech_slug === slug);
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const t = findTech(slug);
  if (!t) return {};
  return {
    title: `${t.tech_name} — demand and who hires for it`,
    description: `${num(t.postings_mentioning)} tracked postings mention ${t.tech_name}.`,
  };
}

export default async function TechPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const t = findTech(slug);
  if (!t) notFound();

  const employers = techData()
    .TECH_EMPLOYERS.filter((e) => e.tech_slug === slug)
    .slice(0, 8);
  const pairs = (groupBy(marketData().STACK_PAIRS, (p) => p.tech_slug).get(slug) ?? []).slice(
    0,
    8,
  );
  const series = history()
    .TECH_HISTORY.filter((h) => h.tech_slug === slug)
    .map((h) => ({ date: h.observed_date, value: h.postings_mentioning }));

  return (
    <Page>
      <PageHeader
        title={t.tech_name}
        sub={
          <>
            {t.category}
            {t.overall_rank ? ` · #${t.overall_rank} by US openings` : ""}
            {t.category_rank ? ` · #${t.category_rank} in ${t.category}` : ""}
          </>
        }
      />

      <Kpis>
        <KpiTile label="Tracked postings" value={num(t.postings_mentioning)} />
        {t.openings ? <KpiTile label="US openings" value={num(t.openings)} /> : null}
        {t.pct_of_category != null ? (
          <KpiTile label="Share of category" value={pct(t.pct_of_category)} />
        ) : null}
        {t.pct_top_band != null ? (
          <KpiTile
            label="In the top salary band"
            value={pct(t.pct_top_band)}
            note={t.salary_sample ? `${num(t.salary_sample)} postings with a figure` : undefined}
          />
        ) : null}
      </Kpis>

      {series.length >= 2 && (
        <Section title="Measured demand">
          <Sparkline series={series} />
          <Note>
            One point per day this technology was actually measured, not interpolated. A
            short series is a short series; it is drawn rather than turned into a trend
            claim, because with few measured days a slope describes when collection started
            rather than what the market did.
          </Note>
        </Section>
      )}

      {employers.length > 0 && (
        <Section title="Who hires for it">
          <Table>
            <thead>
              <tr>
                <Th>Company</Th>
                <Th numeric>Postings</Th>
              </tr>
            </thead>
            <tbody>
              {employers.map((e) => (
                <tr key={e.company_name}>
                  <Td>
                    <Link
                      href={`/companies/${slugify(e.company_name)}/`}
                      className="text-text-2 transition-base hover:text-accent"
                    >
                      {e.company_name}
                    </Link>
                  </Td>
                  <Td numeric>{num(e.postings)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Section>
      )}

      {pairs.length > 0 && (
        <Section title="What it travels with">
          <Table>
            <thead>
              <tr>
                <Th>Technology</Th>
                <Th numeric>Share of these postings</Th>
                <Th numeric>Lift</Th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((p) => (
                <tr key={p.co_tech_slug}>
                  <Td>
                    <Link
                      href={`/tech/${p.co_tech_slug}/`}
                      className="text-text-2 transition-base hover:text-accent"
                    >
                      {p.co_tech_name}
                    </Link>
                  </Td>
                  <Td numeric>{pct(p.pct_of_tech_postings)}</Td>
                  <Td numeric>{p.lift ? `${p.lift.toFixed(1)}×` : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Note>
            Lift compares how often the two appear together against how often they would if
            they were unrelated. A lift near 1 means the pairing is a coincidence of
            volume.
          </Note>
        </Section>
      )}

      <p className="mt-7 text-small">
        <Link href="/tech/" className="text-text-3 transition-base hover:text-accent">
          ← All technologies
        </Link>
      </p>
    </Page>
  );
}
