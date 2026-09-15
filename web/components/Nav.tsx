"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cx } from "@/lib/cx";
import { ThemeToggle } from "./ThemeToggle";

/** The same four destinations the site has today. Nothing is added or renamed. */
const LINKS = [
  { href: "/", label: "Jobs" },
  { href: "/companies/", label: "Companies" },
  { href: "/market/", label: "Market" },
  { href: "/agent/", label: "Agent" },
];

export function Nav() {
  const pathname = usePathname() ?? "/";

  const isCurrent = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-page items-center gap-5 px-4 sm:px-5">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-text transition-base hover:text-accent"
        >
          {/* The existing three-bar mark, kept. It is the only piece of visual
              identity the site has and there is no reason to spend it. */}
          <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden>
            <rect x="1" y="8" width="3" height="6" rx="1" fill="currentColor" />
            <rect x="6" y="4.5" width="3" height="9.5" rx="1" fill="currentColor" />
            <rect x="11" y="1" width="3" height="13" rx="1" fill="currentColor" />
          </svg>
          <span className="font-display text-h3 font-semibold tracking-tight">Signal</span>
        </Link>

        <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isCurrent(link.href) ? "page" : undefined}
              className={cx(
                "shrink-0 rounded-md px-3 py-1.5 text-small transition-base",
                isCurrent(link.href)
                  ? "bg-surface text-text"
                  : "text-text-2 hover:bg-surface hover:text-text",
              )}
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <ThemeToggle />
      </div>
    </header>
  );
}
