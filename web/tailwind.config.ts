import type { Config } from "tailwindcss";

/**
 * The design system. Every token the site uses is defined here and nowhere else.
 *
 * Two rules this file exists to enforce:
 *
 *   1. No component hard-codes a colour, a size, a radius or a duration. If a value
 *      is needed and is not below, the answer is to add it here, not to write it
 *      inline - that is how a "system" becomes eleven slightly different greys.
 *   2. Tailwind's own scales are deliberately REPLACED rather than extended for
 *      colour, type, spacing, radius and shadow. Extending leaves `gray-50` and
 *      `text-2xl` reachable, and the moment they are reachable they get used.
 *
 * Both themes are driven by `data-theme` on <html>, written by ThemeProvider. Every
 * colour below is a CSS variable so a single attribute flips the whole site; the
 * hex values live in app/globals.css where both palettes sit side by side and can
 * be compared.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    // --- colour -----------------------------------------------------------
    // Near-monochrome, one accent. Status colours are SEPARATE from the accent:
    // green means "this step passed", not "this is interactive", and conflating
    // them makes a console unreadable.
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",

      bg: "rgb(var(--c-bg) / <alpha-value>)",
      surface: "rgb(var(--c-surface) / <alpha-value>)",
      raised: "rgb(var(--c-raised) / <alpha-value>)",

      line: "rgb(var(--c-line) / <alpha-value>)",
      "line-strong": "rgb(var(--c-line-strong) / <alpha-value>)",

      text: "rgb(var(--c-text) / <alpha-value>)",
      "text-2": "rgb(var(--c-text-2) / <alpha-value>)",
      "text-3": "rgb(var(--c-text-3) / <alpha-value>)",

      accent: {
        DEFAULT: "rgb(var(--c-accent) / <alpha-value>)",
        dim: "rgb(var(--c-accent-dim) / <alpha-value>)",
        fg: "rgb(var(--c-accent-fg) / <alpha-value>)",
      },

      ok: {
        DEFAULT: "rgb(var(--c-ok) / <alpha-value>)",
        dim: "rgb(var(--c-ok-dim) / <alpha-value>)",
      },
      warn: {
        DEFAULT: "rgb(var(--c-warn) / <alpha-value>)",
        dim: "rgb(var(--c-warn-dim) / <alpha-value>)",
      },
      stop: {
        DEFAULT: "rgb(var(--c-stop) / <alpha-value>)",
        dim: "rgb(var(--c-stop-dim) / <alpha-value>)",
      },

      // The console keeps its own ground in BOTH themes. A terminal that turns
      // white stops reading as a terminal, and this panel's whole job is to look
      // like the real thing.
      console: {
        bg: "#0A0C10",
        line: "#1C2128",
        text: "#C9D1D9",
        dim: "#7D8590",
      },
    },

    // --- type -------------------------------------------------------------
    // Newsreader (display) over Archivo (UI), with IBM Plex Mono for every figure.
    // Serif is confined to display sizes: it gives the page a voice without
    // costing anything in a dense table, which is what most of this site is.
    fontFamily: {
      display: ["var(--font-display)", "Georgia", "serif"],
      sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
    },

    // One scale, eight steps, each with its own line-height and tracking. Named
    // for the job rather than the size, so `text-h2` cannot drift into meaning
    // something different on a different page.
    fontSize: {
      "display-1": ["2.75rem", { lineHeight: "1.04", letterSpacing: "-0.03em" }],
      "display-2": ["2rem", { lineHeight: "1.08", letterSpacing: "-0.025em" }],
      h1: ["1.625rem", { lineHeight: "1.15", letterSpacing: "-0.02em" }],
      h2: ["1.25rem", { lineHeight: "1.25", letterSpacing: "-0.015em" }],
      h3: ["1rem", { lineHeight: "1.35", letterSpacing: "-0.01em" }],
      body: ["0.9375rem", { lineHeight: "1.55", letterSpacing: "0" }],
      small: ["0.84375rem", { lineHeight: "1.5", letterSpacing: "0" }],
      micro: ["0.75rem", { lineHeight: "1.4", letterSpacing: "0.02em" }],
      data: ["0.8125rem", { lineHeight: "1.4", letterSpacing: "-0.005em" }],
    },

    fontWeight: {
      normal: "400",
      medium: "500",
      semibold: "600",
    },

    // --- space, shape, depth ----------------------------------------------
    // A 4px grid, named 1-9. Replacing Tailwind's scale means `p-4` is 16px here
    // and there is no `p-3.5` to reach for at 2am.
    spacing: {
      0: "0px",
      px: "1px",
      1: "4px",
      2: "8px",
      3: "12px",
      4: "16px",
      5: "24px",
      6: "32px",
      7: "48px",
      8: "64px",
      9: "96px",
    },

    borderRadius: {
      none: "0",
      sm: "4px",
      md: "6px",
      lg: "10px",
      full: "999px",
    },

    borderWidth: { DEFAULT: "1px", 0: "0", 2: "2px" },

    // Three steps, all restrained. Depth on this site comes from the border and
    // the surface, not from a drop shadow.
    boxShadow: {
      none: "none",
      1: "0 1px 2px rgb(0 0 0 / 0.18)",
      2: "0 2px 8px -2px rgb(0 0 0 / 0.28)",
      3: "0 12px 32px -8px rgb(0 0 0 / 0.38)",
      focus: "0 0 0 2px rgb(var(--c-bg)), 0 0 0 4px rgb(var(--c-accent) / 0.55)",
    },

    maxWidth: {
      page: "1180px", // matches the current site's .wrap, so line lengths do not move
      prose: "720px",
      none: "none",
      full: "100%",
    },

    // --- motion -----------------------------------------------------------
    // Everything under 200ms, one easing curve. Used on hover and focus only;
    // there is no scroll-triggered animation anywhere on this site.
    // Named, not DEFAULT: a DEFAULT key generates a bare `duration` class, which
    // reads as "unset" at a call site rather than as a deliberate 160ms.
    transitionDuration: { fast: "120ms", base: "160ms", slow: "200ms" },
    transitionTimingFunction: { out: "cubic-bezier(.2,.6,.2,1)" },

    extend: {
      gridTemplateColumns: {
        filters: "repeat(auto-fit, minmax(150px, 1fr))",
      },
      keyframes: {
        blink: { "0%,49%": { opacity: "1" }, "50%,100%": { opacity: "0" } },
        rise: {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        blink: "blink 1s steps(1) infinite",
        rise: "rise 160ms cubic-bezier(.2,.6,.2,1) both",
      },
    },
  },
  plugins: [],
};

export default config;
