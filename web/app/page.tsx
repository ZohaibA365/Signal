import { JobBoard } from "@/components/JobBoard";
import { searchMeta } from "@/lib/data";

export const metadata = {
  title: "Data & AI jobs — US and Canada, updated daily",
};

export default function Page() {
  // The payload's own hash pairs the page with the data it was built against, so a
  // cached page can never render against an incompatible payload. The current site
  // does this with ?v= on the script tag; here it rides on the fetch URL.
  const { hash } = searchMeta();
  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
  return <JobBoard dataUrl={`${base}/data/jobs.json?v=${hash}`} />;
}
