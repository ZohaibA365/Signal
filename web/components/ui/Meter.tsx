import { cx } from "@/lib/cx";

/**
 * The proportion bar used on company and market pages.
 *
 * `dim` exists because these appear in ranked lists where only the top few are the
 * point; drawing all ten at full strength turns a ranking into a wall.
 */
export function Meter({ value, dim = false }: { value: number; dim?: boolean }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <span className="block h-1 w-full overflow-hidden rounded-full bg-line">
      <span
        className={cx("block h-full rounded-full", dim ? "bg-text-3" : "bg-accent")}
        style={{ width: `${pct}%` }}
      />
    </span>
  );
}
