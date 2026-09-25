export const ActDial = ({ rows, initial = 0.9 }) => {
  // rows: [confidence, right (0/1), model answer, gold if wrong, start of the message], sorted by confidence
  const [act, setAct] = useState(initial);
  const [picked, setPicked] = useState(null);
  const cols = 35, size = 11, r = 4;
  const order = [...rows].reverse(); // most confident first: automated rows fill the grid from the top
  const auto = order.filter((x) => x[0] >= act);
  const wrong = auto.filter((x) => !x[1]).length;
  const caught = order.length - auto.length - order.filter((x) => x[0] < act && x[1]).length;
  const pct = (a, b) => (b ? ((100 * a) / b).toFixed(1) : "0.0") + "%";
  const color = (x) => (x[0] >= act ? (x[1] ? "#7D969B" : "#C64D35") : "rgba(128,128,128,0.3)");
  const height = Math.ceil(order.length / cols) * size;
  const p = picked !== null ? order[picked] : null;
  return (
    <div className="not-prose hunch-widget" style={{ border: "1px solid rgba(128,128,128,0.25)", borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-widget circle{transition:fill .15s} @media (prefers-reduced-motion: reduce){.hunch-widget circle{transition:none}}`}</style>
      <label style={{ display: "block", fontSize: 14 }}>
        <code>act: {act.toFixed(2)}</code>
        <input type="range" min="0.5" max="0.99" step="0.01" value={act} onChange={(e) => setAct(Number(e.target.value))}
          style={{ width: "100%", accentColor: "#7D969B" }} aria-label="act threshold" />
      </label>
      <svg viewBox={`0 0 ${cols * size} ${height}`} style={{ width: "100%", display: "block", margin: "8px 0" }} role="img"
        aria-label={`${auto.length} of ${order.length} answers automated, ${wrong} of them wrong`}>
        {order.map((x, i) => (
          <circle key={i} cx={(i % cols) * size + size / 2} cy={Math.floor(i / cols) * size + size / 2} r={r}
            fill={color(x)} stroke={x[0] < act && !x[1] ? "#C64D35" : "none"} strokeWidth="1.2"
            onMouseEnter={() => setPicked(i)} onClick={() => setPicked(i)} style={{ cursor: "pointer" }} />
        ))}
      </svg>
      <div style={{ fontSize: 14, lineHeight: 1.6, display: "flex", flexWrap: "wrap", columnGap: 16 }}>
        <span style={{ whiteSpace: "nowrap" }}><span style={{ color: "#7D969B" }}>●</span> automated, right</span>
        <span style={{ whiteSpace: "nowrap" }}><span style={{ color: "#C64D35" }}>●</span> automated, wrong</span>
        <span style={{ whiteSpace: "nowrap" }}><span style={{ color: "rgba(128,128,128,0.6)" }}>●</span> to a person</span>
        <span style={{ whiteSpace: "nowrap" }}><span style={{ color: "#C64D35" }}>○</span> a mistake the person catches</span>
      </div>
      <p style={{ fontSize: 15, margin: "10px 0 0" }}>
        <b>{auto.length}</b> of {order.length} answers automated ({pct(auto.length, order.length)}),{" "}
        <b>{wrong}</b> of them wrong ({pct(wrong, auto.length)}). {order.length - auto.length} go to a person, who catches {caught} mistakes.
      </p>
      <p style={{ fontSize: 14, margin: "8px 0 0", minHeight: 44, opacity: p ? 1 : 0.6 }}>
        {p ? (<>“{p[4]}{p[4].length >= 70 ? "…" : ""}” <br />model: <code>{p[2]}</code> at {p[0].toFixed(2)}
          {p[1] ? " (right)" : <> · gold: <code>{p[3]}</code></>}</>) : "Point at a dot to read the message."}
      </p>
    </div>
  );
};
