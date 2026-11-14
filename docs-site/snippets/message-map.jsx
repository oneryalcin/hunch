export const MessageMap = ({ map }) => {
  // map.groups: [intent, its description]; map.points: [x, y, group, start of the message], x and y on one 0-1000
  // scale and sorted left to right, so the slider sweeps across the map
  const [picked, setPicked] = useState(0);
  const { groups, points } = map;
  const pad = 24, r = 10;
  const width = 1000 + 2 * pad, height = Math.max(...points.map((p) => p[1])) + 2 * pad;
  // Colour and shape both name the intent, so neither is needed alone. The two card intents share a hue, filled
  // and hollow: they mean nearly the same thing.
  const shape = (g, s) => [
    <rect className="m0" x={-s * 0.85} y={-s * 0.85} width={s * 1.7} height={s * 1.7} rx="2" />,
    <path className="m1" d={`M0 ${-s * 1.2} L${s * 1.2} 0 L0 ${s * 1.2} L${-s * 1.2} 0 Z`} />,
    <circle className="m2" r={s} />,
    <circle className="m3" r={s - 1.5} />,
  ][g];
  const p = points[picked];
  return (
    <div className="not-prose hunch-widget hunch-map" style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-map .m0{fill:#eb6834} .hunch-map .m1{fill:#1baf7a} .hunch-map .m2{fill:#2a78d6}
        .hunch-map .m3{fill:none;stroke:#2a78d6;stroke-width:3}
        .dark .hunch-map .m0{fill:#d95926} .dark .hunch-map .m1{fill:#199e70} .dark .hunch-map .m2{fill:#3987e5}
        .dark .hunch-map .m3{stroke:#3987e5}`}</style>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", display: "block" }} role="img"
        aria-label={`${points.length} bank messages as points. Messages of one intent sit together; the two card delivery intents overlap.`}>
        {points.map((q, i) => (
          <g key={i} transform={`translate(${q[0] + pad} ${q[1] + pad})`} opacity={i === picked ? 1 : 0.8}
            onMouseEnter={() => setPicked(i)} onClick={() => setPicked(i)} style={{ cursor: "pointer" }}>
            <circle r={r * 1.6} fill="transparent" />
            {shape(q[2], r)}
          </g>
        ))}
        <circle cx={p[0] + pad} cy={p[1] + pad} r={r * 2} fill="none" stroke="currentColor" strokeWidth="3" pointerEvents="none" />
      </svg>
      <div style={{ fontSize: 14, lineHeight: 1.5, marginTop: 8 }}>
        {groups.map(([name, about], g) => (
          <div key={name} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <svg viewBox="-12 -12 24 24" width="14" height="14" style={{ flex: "none", position: "relative", top: 2 }} aria-hidden="true">{shape(g, 8)}</svg>
            <span><code>{name}</code> {about}</span>
          </div>
        ))}
      </div>
      <label style={{ display: "block", fontSize: 14, marginTop: 12 }}>
        Read message {picked + 1} of {points.length}, left to right
        <input type="range" min="1" max={points.length} value={picked + 1}
          onChange={(e) => setPicked(Number(e.target.value) - 1)}
          style={{ width: "100%", accentColor: "#7D969B" }}
          aria-label="Message to read" aria-valuetext={`Message ${picked + 1} of ${points.length}`} />
      </label>
      <p style={{ fontSize: 14, margin: "8px 0 0", minHeight: 44 }}>
        “{p[3]}{p[3].length >= 70 ? "…" : ""}” <br />Jev's answer: <code>{groups[p[2]][0]}</code>
      </p>
    </div>
  );
};
