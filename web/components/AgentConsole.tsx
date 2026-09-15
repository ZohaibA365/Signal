"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Input, Textarea } from "@/components/ui/Field";
import { cx } from "@/lib/cx";
import {
  MODE,
  useAgentStream,
  type AgentEvent,
  type Scenario,
} from "@/hooks/useAgentStream";

/**
 * The console. Presentation only - every rule lives in useAgentStream.
 *
 * The panel keeps a mono face and its own near-black ground in both themes. A
 * terminal that turns white stops reading as a terminal, and the credibility of this
 * page rests entirely on it looking like the real thing rather than like a product
 * illustration of one.
 */
const PRESETS = [
  { id: "already_contacted", label: "Company already contacted" },
  { id: "wrong_company", label: "Wrong company named" },
  { id: "unknown_posting", label: "A job that doesn't exist" },
];

export function AgentConsole({ api, scenarios }: { api: string; scenarios: Scenario[] }) {
  const s = useAgentStream(api, scenarios);
  const [posting, setPosting] = useState("");
  const [sender, setSender] = useState({ name: "", program: "", school: "", term: "" });
  const logRef = useRef<HTMLDivElement>(null);

  // Play the real run once on arrival so the panel is not an empty box. It is a
  // sample and the line under it says so: nothing has been attempted yet.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !scenarios.length) return;
    started.current = true;
    s.replay(scenarios.find((x) => x.id === "real") ?? scenarios[0], MODE.arrival);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarios]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [s.lines, s.draft]);

  return (
    <div className="grid gap-5">
      <div className="grid gap-2">
        <Textarea
          value={posting}
          onChange={(e) => setPosting(e.target.value)}
          placeholder="Paste the job posting here — or its link, or just the company name"
          rows={3}
          aria-label="Job posting"
        />
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
            <Input
              value={sender.name}
              onChange={(e) => setSender({ ...sender, name: e.target.value })}
              placeholder="Your name"
              maxLength={60}
              aria-label="Your name"
            />
            <Input
              value={sender.program}
              onChange={(e) => setSender({ ...sender, program: e.target.value })}
              placeholder="What you do"
              maxLength={80}
              aria-label="What you do"
            />
            <Input
              value={sender.school}
              onChange={(e) => setSender({ ...sender, school: e.target.value })}
              placeholder="Where"
              maxLength={80}
              aria-label="Where"
            />
            <Input
              value={sender.term}
              onChange={(e) => setSender({ ...sender, term: e.target.value })}
              placeholder="Looking for"
              maxLength={40}
              aria-label="Looking for"
            />
          </div>
          <Button
            variant="primary"
            onClick={() =>
              s.run(
                posting.trim(),
                Object.fromEntries(
                  Object.entries(sender).filter(([, v]) => v.trim()),
                ),
              )
            }
            disabled={s.running}
            className="sm:w-28"
          >
            {s.running ? "Running…" : "Run"}
          </Button>
        </div>
      </div>

      <div
        ref={logRef}
        className="max-h-[420px] overflow-y-auto rounded-lg border border-console-line bg-console-bg p-4 font-mono text-data leading-relaxed"
      >
        {s.lines.map((event, i) => (
          <Line key={i} event={event} />
        ))}
        {s.running && <span className="ml-1 inline-block animate-blink text-accent">▋</span>}
      </div>

      <p className="text-small text-text-3">{s.mode}</p>

      {s.draft && (
        <div className="whitespace-pre-wrap rounded-lg border border-line bg-surface p-4 font-mono text-data text-text">
          {s.draft}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-small text-text-2">Or watch it refuse to make something up:</span>
        {PRESETS.map((p) => {
          const scenario = scenarios.find((x) => x.id === p.id);
          if (!scenario) return null;
          return (
            <Chip key={p.id} onClick={() => s.replay(scenario)}>
              {p.label}
            </Chip>
          );
        })}
      </div>
    </div>
  );
}

/**
 * One line. Colour carries meaning and nothing else: green is a step that passed,
 * amber a safety rail that fired, red a failure. The accent is not used here, so
 * "interactive" and "this went well" cannot be confused.
 */
function Line({ event }: { event: AgentEvent }) {
  if (event.kind === "done") {
    return (
      <div className="mt-2 border-t border-console-line pt-2 text-accent">
        {event.note}
      </div>
    );
  }

  if (event.kind === "resolved" || event.kind === "note") {
    return <Row mark="·" className="text-console-dim">{event.note}</Row>;
  }

  if (event.kind === "unavailable") {
    return (
      <>
        <Row mark="▲" className="text-warn">
          {event.note}
        </Row>
        {event.suggestions?.length ? (
          <Row mark="·" className="text-console-dim">
            Try one of these instead: {event.suggestions.join(", ")}
          </Row>
        ) : null}
      </>
    );
  }

  if (event.kind === "step") {
    const tone =
      event.status === "ok"
        ? "text-ok"
        : event.status === "rejected"
          ? "text-warn"
          : "text-stop";
    const mark = event.status === "ok" ? "✓" : event.status === "rejected" ? "▲" : "✕";
    return (
      <>
        {event.before ? (
          <Row mark="·" className="text-console-dim">
            {event.before}
          </Row>
        ) : null}
        <Row mark={mark} className={tone}>
          {event.note}
        </Row>
        {event.detail ? (
          <div className="pl-5 text-console-dim opacity-80">{event.detail}</div>
        ) : null}
      </>
    );
  }

  return null;
}

function Row({
  mark,
  className,
  children,
}: {
  mark: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cx("flex gap-2 animate-rise", className)}>
      <span aria-hidden className="w-3 shrink-0 text-center opacity-70">
        {mark}
      </span>
      <span className="min-w-0 flex-1 text-console-text">{children}</span>
    </div>
  );
}
