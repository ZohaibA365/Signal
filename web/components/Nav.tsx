"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cx } from "@/lib/cx";
import { ThemeToggle } from "./ThemeToggle";

const LINKS = [
  { href: "/", label: "Jobs" },
  { href: "/companies/", label: "Companies" },
  { href: "/market/", label: "Market" },
  { href: "/agent/", label: "Agent" },
];

/**
 * A masthead rather than an app bar.
 *
 * The first version used pill-shaped nav buttons, which is what every dashboard
 * looks like and was a large part of why the redesign read as the same site. These
 * are letter-spaced small caps with a rule under the current one - the typographic
 * treatment a publication uses, which is what this site is closer to being.
 */
export function Nav() {
  const pathname = usePathname() ?? "/";
  const isCurrent = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header // Opaque. A translucent blurred bar is glassmorphism, which this design
      // does not use, and it makes the text under it unreadable while scrolling.
      className="sticky top-0 z-20 border-b border-line bg-bg">
      <div className="mx-auto flex h-14 max-w-page items-center gap-4 px-4 sm:gap-6 sm:px-5">
        <Link href="/" className="group flex shrink-0 items-baseline gap-2">
          <span className="font-display text-h2 font-semibold leading-none tracking-tight text-text transition-base group-hover:text-accent">
            Signal
          </span>
          <span className="hidden text-label uppercase text-text-3 sm:inline">
            hiring data
          </span>
        </Link>

        <nav className="flex min-w-0 flex-1 items-center gap-3 overflow-x-auto sm:gap-5">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isCurrent(link.href) ? "page" : undefined}
              className={cx(
                "relative shrink-0 py-4 text-label uppercase transition-base",
                isCurrent(link.href)
                  ? "text-text after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-accent"
                  : "text-text-3 hover:text-text",
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
