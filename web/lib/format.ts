/**
 * Number and date formatting, in one place.
 *
 * The Jinja build had a single `fmt` filter that rendered None as an em-dash. The
 * same rule holds here: a missing figure is shown as "—" and never as "0", because
 * "we did not measure this" and "we measured zero" are different claims and this
 * site's whole argument is that it does not overstate what it knows.
 */
export const DASH = "—";

export function num(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return Math.round(value).toLocaleString("en-US");
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return `${value.toFixed(digits)}%`;
}

export function money(value: number | null | undefined): string {
  if (!value) return DASH;
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

/** "3d" / "2w" / "4mo" — the age of a posting, in the least space that reads. */
export function age(days: number | null | undefined): string {
  if (days == null) return DASH;
  if (days <= 0) return "today";
  if (days === 1) return "1d";
  if (days < 14) return `${days}d`;
  if (days < 60) return `${Math.round(days / 7)}w`;
  return `${Math.round(days / 30)}mo`;
}

export function isoDate(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Date(value).toISOString().slice(0, 10);
}
