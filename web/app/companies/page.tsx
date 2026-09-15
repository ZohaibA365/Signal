import Link from "next/link";
import { Page, PageHeader, Note } from "@/components/ui/Page";
import { Table, Th, Td } from "@/components/ui/Table";
import { num } from "@/lib/format";
import { companies, slugify } from "@/lib/site";

export const metadata = {
  title: "Companies",
  description: "Every employer with at least five tracked roles, and what they hire for.",
};

export default function CompaniesIndex() {
  const rows = companies();
  return (
    <Page>
      <PageHeader
        title="Companies"
        sub={`${num(rows.length)} employers with at least five tracked roles.`}
      />
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
          {rows.map((c) => (
            <tr key={c.company_name} className="transition-base hover:bg-surface">
              <Td>
                <Link
                  href={`/companies/${slugify(c.company_name)}/`}
                  className="text-text transition-base hover:text-accent"
                >
                  {c.company_name}
                </Link>
              </Td>
              <Td numeric>{num(c.total_postings)}</Td>
              <Td numeric>{num(c.postings_last_30d)}</Td>
              <Td numeric>{c.distinct_states ?? "—"}</Td>
              <Td numeric>{c.total_filings ? num(c.total_filings) : "—"}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <Note>
        Ordered by tracked roles. Visa filings are labour condition applications matched
        to the employer in Department of Labor data, shown only where the match is
        confident enough to state as fact.
      </Note>
    </Page>
  );
}
