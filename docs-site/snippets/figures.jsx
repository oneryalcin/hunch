export const ReviewLoop = () => {
  // The work loop, drawn as a cycle. Static: no state, no motion.
  const steps = [["run", "ask every row"], ["review", "a sample"], ["test", "how often right"], ["change", "the spec"], ["diff", "what flipped"]];
  const cx = 230, cy = 165, R = 135;
  const at = (i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / steps.length;
    return [cx + R * Math.cos(a), cy + R * Math.sin(a)];
  };
  const arc = (i) => {
    const a0 = -Math.PI / 2 + (2 * Math.PI * (i + 0.33)) / steps.length, a1 = -Math.PI / 2 + (2 * Math.PI * (i + 0.67)) / steps.length;
    return `M ${cx + R * Math.cos(a0)} ${cy + R * Math.sin(a0)} A ${R} ${R} 0 0 1 ${cx + R * Math.cos(a1)} ${cy + R * Math.sin(a1)}`;
  };
  return (
    <figure className="not-prose" style={{ margin: "16px 0" }}>
      <svg viewBox="0 0 460 330" style={{ width: "100%", maxWidth: 480, display: "block", margin: "0 auto" }} role="img"
        aria-label="A loop: run, review, test, change the spec, diff, then run again">
        <defs>
          <marker id="hunch-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#B45309" />
          </marker>
        </defs>
        {steps.map((_, i) => (
          <path key={"a" + i} d={arc(i)} fill="none" stroke="#B45309" strokeOpacity="0.7" strokeWidth="1.5" markerEnd="url(#hunch-arrow)" />
        ))}
        {steps.map(([name, what], i) => {
          const [x, y] = at(i);
          return (
            <g key={name}>
              <rect x={x - 50} y={y - 21} width="100" height="42" rx="10" fill="#F59E0B" fillOpacity="0.14" stroke="#B45309" strokeOpacity="0.5" />
              <text x={x} y={y - 3} textAnchor="middle" fontSize="14" fontWeight="600" fill="currentColor" fontFamily="ui-monospace, monospace">{name}</text>
              <text x={x} y={y + 13} textAnchor="middle" fontSize="10.5" fill="currentColor" fillOpacity="0.65">{what}</text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
};

export const AccuracyGroups = ({ agree = 340, disagree = 45, sampled = 60 }) => {
  // Rows with an answer key, split by whether the model agrees with it. Real BANKING77 counts by default.
  const cols = 35, size = 11, r = 4;
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const picked = new Set();
  while (picked.size < Math.min(sampled, agree)) picked.add(Math.floor(rand() * agree));
  const grid = (n, fill) => {
    const rows = Math.ceil(n / cols);
    return { h: rows * size, dots: Array.from({ length: n }, (_, i) => ({ x: (i % cols) * size + size / 2, y: Math.floor(i / cols) * size + size / 2, on: fill(i) })) };
  };
  const a = grid(agree, (i) => picked.has(i)), d = grid(disagree, () => true);
  const gap = 34, top = 20, W = cols * size;
  const H = top + a.h + gap + d.h + 4;
  const dot = (p, i, dy) => (
    <circle key={dy + "-" + i} cx={p.x} cy={p.y + dy} r={r} fill={p.on ? "#F59E0B" : "rgba(128,128,128,0.3)"} />
  );
  return (
    <figure className="not-prose" style={{ margin: "16px 0" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 460, display: "block", margin: "0 auto" }} role="img"
        aria-label={`${agree} rows where model and key agree, ${sampled} of them reviewed at random; ${disagree} rows where they disagree, all reviewed`}>
        <text x="0" y="13" fontSize="11" fill="currentColor">Model and key agree: {agree} rows, a random {sampled} reviewed</text>
        {a.dots.map((p, i) => dot(p, i, top))}
        <text x="0" y={top + a.h + gap - 8} fontSize="11" fill="currentColor">They disagree: {disagree} rows, all reviewed</text>
        {d.dots.map((p, i) => dot(p, i, top + a.h + gap))}
      </svg>
      <figcaption style={{ fontSize: 13, textAlign: "center", opacity: 0.7, marginTop: 6 }}>
        <span style={{ color: "#F59E0B" }}>●</span> reviewed
      </figcaption>
    </figure>
  );
};
