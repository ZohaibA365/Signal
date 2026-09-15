import Link from "next/link";
import { Note, Page, PageHeader } from "@/components/ui/Page";
import { Table, Td, Th } from "@/components/ui/Table";
import { num, pct } from "@/lib/format";
import { techData } from "@/lib/site";

export const metadata = {
  title: "Technologies",
  description: "Every technology the postings are scanned for, and how often it appears.",
};

export default function TechIndex() {
  const rows = techData().TECH_DETAIL;
  return (
    <Page>
      <PageHeader
        title="Technologies"
        sub={`${num(rows.length)} tools extracted from posting text, ranked by how many postings name them.`}
      />
      <Table>
        <thead>
          <tr>
            <Th>Technology</Th>
            <Th>Category</Th>
            <Th numeric>Postings</Th>
            <Th numeric>US openings</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.tech_slug} className="transition-base hover:bg-surface">
              <Td>
                <Link
                  href={`/tech/${t.tech_slug}/`}
                  className="text-text transition-base hover:text-accent"
                >
                  {t.tech_name}
                </Link>
              </Td>
              <Td>{t.category}</Td>
              <Td numeric>{num(t.postings_mentioning)}</Td>
              <Td numeric>{t.openings ? num(t.openings) : "—"}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <Note>
        Technologies are matched against posting text by pattern, not by a model. US
        openings come from the market-wide index and are only available for the tools that
        index tracks.
      </Note>
    </Page>
  );
}
