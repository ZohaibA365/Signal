"use client";
import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

/** A toggle that reads as pressed. Used for the six presets and the state chips. */
export function Chip({
  pressed = false,
  onClick,
  children,
  removable = false,
}: {
  pressed?: boolean;
  onClick?: () => void;
  children: ReactNode;
  removable?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={removable ? undefined : pressed}
      onClick={onClick}
      className={cx(
        "inline-flex items-center gap-2 rounded-full border px-3 h-7 text-micro font-medium",
        "transition-base",
        pressed
          ? "border-accent/40 bg-accent-dim text-accent"
          : "border-line bg-raised text-text-2 hover:border-line-strong hover:text-text",
      )}
    >
      {children}
      {removable ? <span aria-hidden className="text-text-3">×</span> : null}
    </button>
  );
}
