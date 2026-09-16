import { JobBoard } from "@/components/JobBoard";

export const metadata = {
  title: "Data & AI jobs — US and Canada, updated daily",
};

/**
 * Where the searchable roles come from.
 *
 * The payload is 4MB and is rebuilt by the daily pipeline, so it is fetched from
 * wherever that pipeline publishes it rather than committed alongside this app.
 * Two things fall out of that: the repository does not carry a four-megabyte file
 * that changes every day, and this page shows today's roles even on a build from
 * last week.
 */
const DATA_URL =
  process.env.NEXT_PUBLIC_JOBS_URL ??
  "https://zohaiba365.github.io/Signal/data/jobs.json";

export default function Page() {
  return <JobBoard dataUrl={DATA_URL} />;
}
