import { cx } from "@/lib/cx";
import type { ReactNode } from "react";

/**
 * The status pill, carrying the meanings the old .tag classes carried.
 *
 * `ok` and `no` are claims about work authorisation and are the reason this exists:
 * a visitor scanning for roles they can legally take reads these before anything
 * else, so they get real colour. `neutral` and `level` are descriptive and stay
 * quiet - if everything is emphasised, nothing is.
 */
type Tone = "ok" | "no" | "neutral" | "level";

const TONES: Record<Tone, string> = {
  ok: "border-ok/35 bg-ok-dim text-ok",
  no: "border-stop/35 bg-stop-dim text-stop",
  neutral: "border-line bg-surface text-text-3",
  level: "border-warn/30 bg-warn-dim text-warn",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-sm border px-2 py-px text-micro font-medium",
        TONES[tone],
      )}
    >
      {children}
    </span>
  );
}
