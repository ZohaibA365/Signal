import Link from "next/link";
import { KpiTile } from "@/components/ui/Card";
import { Meter } from "@/components/ui/Meter";
import { Kpis, Note, Page, PageHeader, Section } from "@/components/ui/Page";
import { Table, Td, Th } from "@/components/ui/Table";
import { num, pct } from "@/lib/format";
import { stats } from "@/lib/data";
import { marketData, withBars } from "@/lib/site";

export const metadata = {
  title: "Market index",
  description: "US demand by technology, what each skill pays, and which tools travel together.",
};

export default function MarketPage() {
  const { DEMAND_BY_CATEGORY, SALARY_LEADERS, STACK_PAIRS } = marketData();
  const { corpus } = stats();

  const categories = new Map<string, typeof DEMAND_BY_CATEGORY>();
  for (const row of DEMAND_BY_CATEGORY) {
    const list = categories.get(row.category);
    if (list) list.push(row);
    else categories.set(row.category, [row]);
  }

  return (
    <Page>
      <PageHeader
        title="Market index"
        sub="US openings by technology, measured daily from a market-wide index rather than from the postings collected here."
      />

      <Kpis>
        <KpiTile label="Postings tracked" value={num(corpus.postings)} />
        <KpiTile label="Employers" value={num(corpus.companies)} />
        <KpiTile label="Technologies" value={num(corpus.technologies)} />
        <KpiTile label="Visa filings" value={num(corpus.total_filings)} />
      </Kpis>

      {[...categories.entries()].map(([category, rows]) => {
        const bars = withBars(rows.slice(0, 10), (r) => r.openings);
        return (
          <Section key={category} title={category}>
            <Table>
              <thead>
                <tr>
                  <Th>Technology</Th>
                  <Th>Share of category</Th>
                  <Th numeric>US openings</Th>
                </tr>
              </thead>
              <tbody>
                {bars.map((r, i) => (
                  <tr key={r.tech_slug}>
                    <Td>
                      <Link
                        href={`/tech/${r.tech_slug}/`}
                        className="text-text-2 transition-base hover:text-accent"
                      >
                        {r.tech_name}
                      </Link>
                    </Td>
                    <Td className="w-[46%]">
                      <Meter value={r.bar} dim={i > 2} />
                    </Td>
                    <Td numeric>{num(r.openings)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Section>
        );
      })}

      <Section title="What each skill pays">
        <Table>
          <thead>
            <tr>
              <Th>Technology</Th>
              <Th numeric>In the top salary band</Th>
              <Th numeric>Postings</Th>
            </tr>
          </thead>
          <tbody>
            {SALARY_LEADERS.slice(0, 15).map((r) => (
              <tr key={r.tech_slug}>
                <Td>
                  <Link
                    href={`/tech/${r.tech_slug}/`}
                    className="text-text-2 transition-base hover:text-accent"
                  >
                    {r.tech_name}
                  </Link>
                </Td>
                <Td numeric>{pct(r.pct_top_band)}</Td>
                <Td numeric>{num(r.total_postings)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        <Note>
          The share of postings naming this technology that fall in the highest salary band
          the source reports. It is not an average salary, and it says nothing about what
          any individual role pays.
        </Note>
      </Section>

      <Section title="Tools that travel together">
        <Table>
          <thead>
            <tr>
              <Th>Technology</Th>
              <Th>Appears with</Th>
              <Th numeric>Share</Th>
              <Th numeric>Lift</Th>
            </tr>
          </thead>
          <tbody>
            {STACK_PAIRS.slice(0, 12).map((p) => (
              <tr key={`${p.tech_slug}-${p.co_tech_slug}`}>
                <Td>
                  <Link
                    href={`/tech/${p.tech_slug}/`}
                    className="text-text-2 transition-base hover:text-accent"
                  >
                    {p.tech_name}
                  </Link>
                </Td>
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
      </Section>
    </Page>
  );
}
