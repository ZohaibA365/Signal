import { cx } from "@/lib/cx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * The only button on the site.
 *
 * Variants are named for intent, not appearance, so a page cannot ask for "the
 * green one" and quietly mean something different from the next page. Sizes are
 * two, because a third is how a system starts drifting.
 */
type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-fg border-accent hover:bg-accent/90 active:bg-accent/80",
  secondary:
    "bg-raised text-text border-line hover:border-line-strong hover:bg-surface",
  ghost:
    "bg-transparent text-text-2 border-transparent hover:text-text hover:bg-surface",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 px-3 text-micro",
  md: "h-9 px-4 text-small",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md border font-medium",
        "transition-base disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
