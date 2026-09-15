import { cx } from "@/lib/cx";
import type {
  InputHTMLAttributes,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
  ReactNode,
} from "react";

/**
 * Input and Select, sharing one shell so they line up in a filter row.
 *
 * The height, radius, border and focus treatment are defined once here. A filter
 * bar where the select is one pixel taller than the input is the difference
 * between a page that feels built and one that feels assembled.
 */
const SHELL =
  "h-9 w-full rounded-md border border-line bg-raised px-3 text-small text-text " +
  "placeholder:text-text-3 transition-base hover:border-line-strong " +
  "focus:border-accent focus:outline-none";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(SHELL, className)} {...rest} />;
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select
      className={cx(SHELL, "cursor-pointer appearance-none pr-8", className)}
      style={{
        // The chevron is drawn rather than imported: the site uses no icon pack,
        // and one caret does not justify one.
        backgroundImage:
          "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none'><path d='M1 1l4 4 4-4' stroke='%23667079' stroke-width='1.5' stroke-linecap='round'/></svg>\")",
        backgroundRepeat: "no-repeat",
        backgroundPosition: "right 12px center",
      }}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <span className="text-micro font-medium uppercase text-text-3">{children}</span>
  );
}

export function Textarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  // The same shell, with the fixed height relaxed: this one grows with its rows.
  return <textarea className={cx(SHELL, "h-auto py-2 leading-relaxed", className)} {...rest} />;
}
