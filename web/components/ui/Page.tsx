import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

/** One page frame, so margins and content width cannot drift between routes. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("mx-auto max-w-page px-4 py-8 sm:px-5", className)}>{children}</div>
  );
}

/**
 * The page's opening.
 *
 * A serif name at display size over a rule, with the numbers that describe it set
 * in mono beneath. The first version was a heading and a grey sentence, which is
 * what the old site already had - the contrast between the serif and the mono strip
 * is what makes this read as a measured thing rather than a dashboard header.
 */
export function PageHeader({
  title,
  sub,
  lead,
}: {
  title: string;
  sub?: ReactNode;
  lead?: ReactNode;
}) {
  // No eyebrow label above the title. An earlier version put a small-caps accent
  // word over every headline - "Employer", "Technology", "Directory" - which is
  // the badge-above-the-headline pattern, and it was on the list of things this
  // design is explicitly not allowed to do. What kind of page this is, is already
  // said by the nav, the URL and the title itself.
  return (
    <header className="border-b border-line pb-6">
      <h1 className="font-display text-display-2 font-medium text-text sm:text-display-1">
        {title}
      </h1>
      {sub ? (
        <p className="tabular mt-4 font-mono text-data text-text-2">{sub}</p>
      ) : null}
      {lead ? (
        <p className="mt-5 max-w-prose font-display text-h3 font-normal italic text-text-2">
          {lead}
        </p>
      ) : null}
    </header>
  );
}

/**
 * A section, opened by a small-caps label against a rule rather than a heading.
 *
 * This is the site's main structural device and the reason the pages read as one
 * system: every section on every page opens the same way.
 */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="mb-4 flex items-center gap-4 text-label uppercase text-text-3">
        <span className="shrink-0">{title}</span>
        <span aria-hidden className="h-px flex-1 bg-line" />
      </h2>
      {children}
    </section>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="mt-4 max-w-prose border-l border-line pl-4 text-small text-text-3">
      {children}
    </p>
  );
}

/**
 * The figures that describe a page, as a ruled strip rather than a row of cards.
 *
 * Cards put a border around every number and make four facts look like four
 * products. A strip of hairline-separated figures is how a reference page states
 * them, and it is far quieter.
 */
export function Kpis({ children }: { children: ReactNode }) {
  return (
    <dl className="mt-6 grid grid-cols-2 divide-line border-y border-line sm:grid-cols-4 sm:divide-x">
      {children}
    </dl>
  );
}
