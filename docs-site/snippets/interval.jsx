export const IntervalSlider = ({ rate = 0.9, initial = 30 }) => {
  // Wilson score interval, as in core.wilson
  const wilson = (k, n, z = 1.96) => {
    const p = k / n, d = 1 + (z * z) / n;
    const c = p + (z * z) / (2 * n), h = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
    return [(c - h) / d, (c + h) / d];
  };
  const toN = (s) => Math.round(10 * Math.pow(50, s / 100)); // slider 0..100 -> 10..500 reviews
  const toS = (n) => (100 * Math.log(n / 10)) / Math.log(50);
  const [s, setS] = useState(toS(initial));
  const n = toN(s), k = Math.round(rate * n);
  const [lo, hi] = wilson(k, n);
  const W = 400, x = (v) => ((v - 0.5) / 0.5) * W; // axis from 50% to 100%
  const pct = (v) => (100 * v).toFixed(1) + "%";
  return (
    <div className="not-prose hunch-widget" style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-widget rect,.hunch-widget circle{transition:all .15s} @media (prefers-reduced-motion: reduce){.hunch-widget rect,.hunch-widget circle{transition:none}}`}</style>
      <label style={{ display: "block", fontSize: 14 }}>
        Reviewed rows: <b>{n}</b>
        <input type="range" min="0" max="100" step="0.5" value={s} onChange={(e) => setS(Number(e.target.value))}
          style={{ width: "100%", accentColor: "#B45309" }} aria-label="reviewed rows" />
      </label>
      <svg viewBox={`-20 0 ${W + 40} 56`} style={{ width: "100%", display: "block", margin: "8px 0" }} role="img"
        aria-label={`${k} of ${n} right; 95% interval ${pct(lo)} to ${pct(hi)}`}>
        <line x1="0" x2={W} y1="20" y2="20" stroke="currentColor" strokeOpacity="0.25" />
        <rect x={x(Math.max(lo, 0.5))} y="12" width={x(hi) - x(Math.max(lo, 0.5))} height="16" rx="8" fill="#F59E0B" fillOpacity="0.35" />
        <circle cx={x(k / n)} cy="20" r="6" fill="#B45309" />
        {[0.5, 0.6, 0.7, 0.8, 0.9, 1].map((v) => (
          <text key={v} x={x(v)} y="48" fontSize="11" textAnchor="middle" fill="currentColor" fillOpacity="0.6">{100 * v}%</text>
        ))}
      </svg>
      <p style={{ fontSize: 15, margin: 0 }}>
        <b>{k}</b> of {n} right: {pct(k / n)}. The true accuracy is likely between <b>{pct(lo)}</b> and <b>{pct(hi)}</b>.
      </p>
    </div>
  );
};
