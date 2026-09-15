import type { MetadataRoute } from "next";
import { companies, slugify, techData } from "@/lib/site";
import { meta } from "@/lib/data";

/**
 * The same 1,243 URLs the Jinja build lists, so search engines see no change.
 *
 * Retired-company redirect stubs are deliberately excluded, exactly as they are
 * today: they exist to catch an old link, not to be indexed as pages.
 */
export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = meta().site_url.replace(/\/$/, "");
  const lastModified = new Date();
  const urls = [
    "/",
    "/market/",
    "/companies/",
    "/tech/",
    "/agent/",
    ...companies().map((c) => `/companies/${slugify(c.company_name)}/`),
    ...techData().TECH_DETAIL.map((t) => `/tech/${t.tech_slug}/`),
  ];
  return urls.map((url) => ({ url: `${base}${url}`, lastModified }));
}
