import type { Metadata } from "next";
import { Newsreader, Archivo, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Nav } from "@/components/Nav";
import { Footer } from "@/components/Footer";
import { meta, stats } from "@/lib/data";

/**
 * Fonts are self-hosted by next/font at build time, not fetched from Google.
 *
 * The current site loads IBM Plex Mono from fonts.googleapis.com, which costs a
 * DNS lookup, a TLS handshake and a render-blocking round trip to a third party
 * before any text appears. next/font emits the files into the bundle and inlines
 * the @font-face, so the page has no third-party dependency at all.
 */
const display = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-display",
  display: "swap",
});

const sans = Archivo({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

const site = meta();

export const metadata: Metadata = {
  metadataBase: new URL(site.site_url),
  title: {
    default: "Signal — data & AI hiring, measured daily",
    template: "%s · Signal",
  },
  description:
    "Every figure traces back to a query. Job postings from company career boards, "
    + "visa filings from the Department of Labor, and a per-technology demand index.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const { corpus } = stats();
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${display.variable} ${sans.variable} ${mono.variable}`}
    >
      <body className="flex min-h-screen flex-col">
        <ThemeProvider>
          <Nav />
          <main className="flex-1">{children}</main>
          <Footer
            postings={corpus.postings}
            companies={corpus.companies}
            filings={corpus.total_filings}
            generatedAt={site.generated_at.slice(0, 10)}
            repoUrl={site.repo_url}
          />
        </ThemeProvider>
      </body>
    </html>
  );
}
