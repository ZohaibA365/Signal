import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

export function Card({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("rounded-lg border border-line bg-surface", className)}>
      {children}
    </div>
  );
}

/**
 * One figure in the ruled strip.
 *
 * The number leads at display size in mono, the label sits under it in small caps.
 * Putting the label first and the number second - which the first version did, and
 * which most dashboards do - makes a row of these read as form fields rather than
 * as the facts the page is about.
 */
export function KpiTile({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
}) {
  return (
    <div className="px-0 py-5 sm:px-5 sm:first:pl-0">
      <dd className="tabular font-mono text-figure text-text">{value}</dd>
      <dt className="mt-2 text-label uppercase text-text-3">{label}</dt>
      {note ? <p className="mt-1 text-small text-text-3">{note}</p> : null}
    </div>
  );
}
