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
 * A single figure with its label. The number is mono and tabular so a row of these
 * lines up on the decimal, which is the entire reason the site loads a mono face.
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
    <Card className="p-4">
      <div className="text-micro font-medium uppercase text-text-3">{label}</div>
      <div className="tabular mt-2 font-mono text-h1 text-text">{value}</div>
      {note ? <div className="mt-1 text-small text-text-2">{note}</div> : null}
    </Card>
  );
}
