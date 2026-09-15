import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

/** One page frame, so margins and content width cannot drift between routes. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("mx-auto max-w-page px-4 py-7 sm:px-5", className)}>{children}</div>
  );
}

export function PageHeader({
  title,
  sub,
  lead,
}: {
  title: string;
  sub?: ReactNode;
  lead?: ReactNode;
}) {
  return (
    <header className="mb-6">
      <h1 className="font-display text-display-2 font-medium text-text">{title}</h1>
      {sub ? <p className="mt-2 text-body text-text-2">{sub}</p> : null}
      {lead ? (
        <p className="mt-4 max-w-prose border-l-2 border-accent/50 pl-4 text-body text-text">
          {lead}
        </p>
      ) : null}
    </header>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-7">
      <h2 className="mb-3 font-display text-h2 font-medium text-text">{title}</h2>
      {children}
    </section>
  );
}

/** The quiet caveat paragraph the site uses to say what a figure does not mean. */
export function Note({ children }: { children: ReactNode }) {
  return <p className="mt-3 max-w-prose text-small text-text-3">{children}</p>;
}

export function Kpis({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{children}</div>
  );
}
