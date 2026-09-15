import fs from "node:fs";
import path from "node:path";
import { AgentConsole } from "@/components/AgentConsole";
import { Note, Page, PageHeader } from "@/components/ui/Page";
import type { Scenario } from "@/hooks/useAgentStream";

export const metadata = {
  title: "Write an outreach email that isn't generic",
  description:
    "Paste a job posting. It checks what the warehouse knows about that employer and writes an email you could send, refusing to state anything it cannot trace.",
};

export default function AgentPage() {
  // The recorded runs ship with the page so the console is never an empty box, even
  // with no service behind it.
  const file = path.join(process.cwd(), "..", "site", "data", "agent_runs.json");
  let scenarios: Scenario[] = [];
  try {
    scenarios = (JSON.parse(fs.readFileSync(file, "utf8")) as { scenarios: Scenario[] })
      .scenarios;
  } catch {
    // A missing recording is not a reason to have no page: the live path still works
    // and the console simply starts empty.
  }

  const api = process.env.NEXT_PUBLIC_AGENT_API ?? "";

  return (
    <Page>
      <PageHeader
        title="Write an outreach email that isn't generic"
        sub="Paste a job posting, a link to one, or just a company name. It looks up what this employer's postings actually say, then writes something you could send."
      />
      <AgentConsole api={api} scenarios={scenarios} />
      <Note>
        It won&rsquo;t write about a company it has no data on, and it won&rsquo;t put a
        number in your email that it can&rsquo;t trace back to a real posting. If there is
        nothing true to say, it says so instead of inventing something.
      </Note>
    </Page>
  );
}
