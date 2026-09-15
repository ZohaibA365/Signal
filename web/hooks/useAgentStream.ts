"use client";

import { useCallback, useRef, useState } from "react";

/**
 * The agent console's connection, parsing and fallback decisions.
 *
 * The component that uses this renders and does nothing else. That split is worth
 * more here than anywhere else on the site: every rule below was learned from a
 * failure, and none of them is visible in the markup.
 *
 * The contract with service/app.py is unchanged. Event kinds, field names and the
 * wording of the `mode` line are carried over verbatim from site/static/agent.js,
 * and agent/events.py remains the single source of the step wording - this asks the
 * server for those strings rather than reimplementing them, so the live run and the
 * recorded ones can never describe the same step differently.
 */

export type AgentEvent =
  | { kind: "resolved"; company?: string; title?: string; fit_score?: number; note?: string }
  | { kind: "note"; note?: string }
  | {
      kind: "step";
      tool?: string;
      status?: "ok" | "rejected" | "failed";
      before?: string;
      note?: string;
      detail?: string;
      ms?: number;
    }
  | { kind: "unavailable"; note?: string; suggestions?: string[] }
  | { kind: "draft"; text: string }
  | { kind: "done"; note?: string; outcome?: string }
  | { kind: "limited"; note?: string }
  | { kind: "error"; note?: string; detail?: string };

export interface Scenario {
  id: string;
  staged: boolean;
  label: string;
  company: string;
  title: string;
  note?: string;
  events: AgentEvent[];
  draft?: string;
}

export interface Sender {
  name?: string;
  program?: string;
  school?: string;
  term?: string;
}

/**
 * How long to wait for the service to START answering before giving up on it.
 *
 * Eight seconds, not two. The endpoint opens a Postgres connection and resolves the
 * pasted text before the first event, and first-byte measures 0.7-1.6s warm - two
 * seconds sat inside that noise, so any cold connection fell back to a recording
 * while the service was working perfectly. This bounds "the service is dead"; the
 * whole-run budget lives on the server.
 */
const LIVE_TIMEOUT_MS = 8000;
const STEP_PAUSE_MS = 620;
const TYPE_MS = 9;
const TYPE_CHUNK = 3;

/** The lines under the console. Each has to be true of how it was reached. */
export const MODE = {
  live: "Live run",
  arrival:
    "A recorded run, so the console is not empty · paste a posting above and press Run to do it live with your own details",
  noService: "Recorded run · no live service is configured for this page",
  failed:
    "Recorded run · the live service did not answer, so no email was written for you — try again shortly",
  staged:
    "Recorded run · the model's misbehaviour here was scripted; the refusal is real",
  recorded: "Recorded run · real agent, real validators",
} as const;

export interface AgentStream {
  lines: AgentEvent[];
  draft: string;
  mode: string;
  running: boolean;
  run: (posting: string, sender: Sender) => void;
  replay: (scenario: Scenario, mode?: string) => void;
}

export function useAgentStream(api: string, scenarios: Scenario[]): AgentStream {
  const [lines, setLines] = useState<AgentEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<string>(MODE.arrival);
  const [running, setRunning] = useState(false);

  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  const clearTimers = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };
  const later = (fn: () => void, ms: number) => {
    timers.current.push(setTimeout(fn, ms));
  };

  const reset = useCallback((note: string) => {
    clearTimers();
    setLines([]);
    setDraft("");
    setMode(note);
  }, []);

  /** Types the draft out rather than pasting it in. Several chars a frame: one at a
      time is slower than reading, and stops feeling like output. */
  const typeOut = useCallback((text: string) => {
    if (!text) return;
    let i = 0;
    const tick = () => {
      if (i >= text.length) return;
      i += TYPE_CHUNK;
      setDraft(text.slice(0, i));
      later(tick, TYPE_MS);
    };
    tick();
  }, []);

  const scenarioById = useCallback(
    (id: string) => scenarios.find((s) => s.id === id) ?? scenarios[0],
    [scenarios],
  );

  const replay = useCallback(
    (scenario: Scenario, why?: string) => {
      if (!scenario) return;
      setRunning(true);
      reset(why ?? (scenario.staged ? MODE.staged : MODE.recorded));

      let i = 0;
      const step = () => {
        if (i >= scenario.events.length) {
          setRunning(false);
          // A recording never types out an email, whoever it was written for.
          // Showing somebody else's message is what made this page feel broken.
          return;
        }
        const event = scenario.events[i++];
        setLines((current) => [...current, event]);
        later(step, STEP_PAUSE_MS);
      };
      step();
    },
    [reset],
  );

  const run = useCallback(
    (posting: string, sender: Sender) => {
      if (running) return;
      if (!api) {
        replay(scenarioById("real"), MODE.noService);
        return;
      }

      let fellBack = false;
      const fallback = () => {
        if (fellBack) return;
        fellBack = true;
        replay(scenarioById("real"), MODE.failed);
      };

      setRunning(true);
      reset(MODE.live);
      // Something on screen before the first event. A second of silence after
      // pressing a button reads as nothing having happened.
      setLines([{ kind: "note", note: "Looking that company up..." }]);

      const controller = new AbortController();
      const guard = setTimeout(() => {
        controller.abort();
        fallback();
      }, LIVE_TIMEOUT_MS);

      fetch(`${api}/api/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posting, sender }),
        signal: controller.signal,
      })
        .then(async (res) => {
          if (!res.ok || !res.body) throw new Error("no stream");
          clearTimeout(guard);

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split("\n\n");
            buffer = parts.pop() ?? "";
            for (const part of parts) {
              // ':' opens an SSE comment. The service sends one every few seconds
              // while the model is writing, so the connection is never idle long
              // enough for a proxy to cut it. There is nothing in it to render.
              if (part.trimStart().startsWith(":")) continue;
              const body = part.replace(/^data: ?/gm, "").trim();
              if (!body) continue;
              let event: AgentEvent;
              try {
                event = JSON.parse(body) as AgentEvent;
              } catch {
                continue; // a malformed frame is not worth ending a run for
              }
              if (event.kind === "draft") {
                typeOut(event.text);
                continue;
              }
              // A refusal to spend, or a server-side error, is indistinguishable
              // from the service being down as far as the visitor is concerned.
              if (event.kind === "limited" || event.kind === "error") {
                fallback();
                return;
              }
              setLines((current) => [...current, event]);
            }
          }
          setRunning(false);
        })
        .catch(() => {
          clearTimeout(guard);
          fallback();
        });
    },
    [api, replay, reset, running, scenarioById, typeOut],
  );

  return { lines, draft, mode, running, run, replay };
}
