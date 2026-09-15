/**
 * Measured demand over time, drawn as SVG.
 *
 * Server-rendered: it is a static path, so there is no reason to ship a charting
 * library or any JavaScript at all for it. The current site draws the same shape
 * with an inline <polyline>; this keeps that and adds the endpoints, because the
 * first and last values are the only two a reader can actually name.
 */
export function Sparkline({
  series,
  width = 560,
  height = 90,
}: {
  series: Array<{ date: string; value: number }>;
  width?: number;
  height?: number;
}) {
  if (series.length < 2) return null;

  const values = series.map((p) => p.value);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const pad = 4;

  const points = series.map((p, i) => {
    const x = (i / (series.length - 1)) * (width - pad * 2) + pad;
    const y = height - pad - ((p.value - low) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const path = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <figure className="rounded-lg border border-line bg-surface p-4">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-[90px] w-full"
        role="img"
        aria-label={`Postings mentioning this technology, ${series[0].date} to ${series[series.length - 1].date}`}
      >
        <polyline
          points={path}
          fill="none"
          stroke="rgb(var(--c-accent))"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle cx={last[0]} cy={last[1]} r="2.5" fill="rgb(var(--c-accent))" />
      </svg>
      <figcaption className="tabular mt-2 flex justify-between font-mono text-micro text-text-3">
        <span>{series[0].date}</span>
        <span>
          {low} – {high} postings
        </span>
        <span>{series[series.length - 1].date}</span>
      </figcaption>
    </figure>
  );
}
