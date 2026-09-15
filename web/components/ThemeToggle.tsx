"use client";
import { useTheme } from "./ThemeProvider";
import { cx } from "@/lib/cx";

/**
 * Two marks, drawn not imported. The site ships no icon pack and one sun does not
 * justify one. Clicking cycles to the opposite explicit theme rather than back to
 * system: once somebody has expressed a preference, silently handing them back to
 * the OS is surprising.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const isLight = theme === "light";
  return (
    <button
      type="button"
      onClick={() => setTheme(isLight ? "dark" : "light")}
      aria-label={isLight ? "Switch to dark theme" : "Switch to light theme"}
      title={isLight ? "Switch to dark theme" : "Switch to light theme"}
      className={cx(
        "grid h-8 w-8 place-items-center rounded-md border border-transparent",
        "text-text-3 transition-base hover:border-line hover:text-text",
      )}
    >
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden>
        {isLight ? (
          <path
            d="M13 9.2A5.8 5.8 0 015.8 2 5.8 5.8 0 108 13.5a5.8 5.8 0 005-4.3z"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
        ) : (
          <>
            <circle cx="7.5" cy="7.5" r="3" stroke="currentColor" strokeWidth="1.3" />
            <path
              d="M7.5 1v1.5M7.5 12.5V14M14 7.5h-1.5M2.5 7.5H1M12.1 2.9l-1 1M3.9 11.1l-1 1M12.1 12.1l-1-1M3.9 3.9l-1-1"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
          </>
        )}
      </svg>
    </button>
  );
}
