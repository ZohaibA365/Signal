/**
 * Static export, so the output is the same shape GitHub Pages already serves.
 *
 * basePath is the repo subpath the site lives at today; trailingSlash reproduces
 * `/companies/<slug>/index.html`, which is the URL shape that is currently indexed.
 * Getting either wrong silently breaks 1,243 live URLs, which is the one thing this
 * rebuild is not allowed to do.
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "/Signal";

export default {
  output: "export",
  basePath,
  trailingSlash: true,
  reactStrictMode: true,
  // No <Image> optimisation is possible in a static export, and the site uses no
  // raster images anyway.
  images: { unoptimized: true },
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
};
