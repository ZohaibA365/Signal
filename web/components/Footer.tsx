import { num } from "@/lib/format";

/**
 * The same three figures the current footer carries, and the same "source" link.
 * They are the site's credentials: how much was collected, from how many employers,
 * against how many visa filings.
 */
export function Footer({
  postings,
  companies,
  filings,
  generatedAt,
  repoUrl,
}: {
  postings: number;
  companies: number;
  filings: number;
  generatedAt: string;
  repoUrl: string;
}) {
  return (
    <footer className="mt-9 border-t border-line">
      <div className="mx-auto flex max-w-page flex-col gap-2 px-4 py-6 text-small text-text-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <p className="tabular font-mono text-data">
          {num(postings)} postings · {num(companies)} employers · {num(filings)} visa filings
        </p>
        <p>
          Updated {generatedAt} ·{" "}
          <a
            href={repoUrl}
            className="text-text-2 underline decoration-line underline-offset-4 transition-base hover:text-accent hover:decoration-accent"
          >
            source
          </a>
        </p>
      </div>
    </footer>
  );
}
