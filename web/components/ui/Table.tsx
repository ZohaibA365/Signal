import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

/**
 * A data table. Wrapped so wide tables scroll inside themselves rather than making
 * the whole page scroll sideways, which on a phone is the difference between a
 * usable table and a broken layout.
 */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <table className="w-full border-collapse text-small">{children}</table>
    </div>
  );
}

export function Th({
  children,
  numeric = false,
}: {
  children: ReactNode;
  numeric?: boolean;
}) {
  return (
    <th
      className={cx(
        "border-b border-line pb-2 text-micro font-medium uppercase text-text-3",
        numeric ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  numeric = false,
  className,
}: {
  children: ReactNode;
  numeric?: boolean;
  className?: string;
}) {
  return (
    <td
      className={cx(
        "border-b border-line py-2 align-middle",
        numeric ? "tabular text-right font-mono text-data text-text" : "text-text-2",
        className,
      )}
    >
      {children}
    </td>
  );
}
