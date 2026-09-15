/**
 * The 157 redirect stubs for companies that have been renamed or merged.
 *
 * Written straight into out/ after the export rather than as Next routes. A retired
 * slug is not a page - it has no content, must not be indexed, and exists only so a
 * link somebody saved a year ago still lands somewhere useful. Making it a route
 * would put it in the sitemap and give it a real <title>, which is the opposite of
 * what it is for.
 *
 *     node scripts/redirect-stubs.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const OUT = path.join(WEB, "out");
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "/Signal";

const slugify = (name) =>
  (name ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";

const { RETIRED_COMPANIES: retired } = JSON.parse(
  fs.readFileSync(path.join(WEB, "data/retired.json"), "utf8"),
);

let written = 0;
for (const row of retired) {
  const from = slugify(row.company_name);
  const to = slugify(row.canonical_name);
  if (!from || from === to) continue;

  const dir = path.join(OUT, "companies", from);
  // Never overwrite a real page. A retired name that is also a live company's slug
  // would otherwise replace that company's page with a redirect to itself.
  if (fs.existsSync(path.join(dir, "index.html"))) continue;

  const target = `${BASE}/companies/${to}/`;
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, "index.html"),
    `<!doctype html><meta charset="utf-8">` +
      `<meta name="robots" content="noindex">` +
      `<link rel="canonical" href="${target}">` +
      `<meta http-equiv="refresh" content="0; url=${target}">` +
      `<title>Moved</title>` +
      `<p>This employer is now listed as <a href="${target}">${row.canonical_name}</a>.</p>\n`,
  );
  written += 1;
}
console.log(`redirect stubs written: ${written}`);
