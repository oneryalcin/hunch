export const AnswerCheck = ({ puzzles }) => {
  // puzzles: [{model, question, passages: [text], answer, p: p(unsupported), passed, marked, why}]
  const [i, setI] = useState(0);
  const [pick, setPick] = useState(null); // true: "something isn't supported", false: "all supported"
  const z = puzzles[i];
  const shown = pick !== null;
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const btn = (label, v) => (
    <button key={label} onClick={() => !shown && setPick(v)} disabled={shown}
      style={{ flex: "1 1 200px", padding: "8px 10px", borderRadius: 8, textAlign: "left",
        border: `1px solid ${shown && v === z.marked ? teal : pick === v ? "currentColor" : grey}`,
        background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: shown ? "default" : "pointer" }}>
      {label}{shown && pick === v ? " · your pick" : ""}
    </button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0", overflowWrap: "anywhere" }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Answer {i + 1} of {puzzles.length}. Does it say anything the passages don't?</div>
      <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0 8px" }}>“{z.question}”</div>
      {z.passages.map((p, k) => (
        <div key={k} style={{ fontSize: 13, lineHeight: 1.5, margin: "6px 0", padding: "6px 10px", borderLeft: `3px solid ${grey}`, opacity: 0.85 }}>
          <span style={{ opacity: 0.6 }}>passage {k + 1} · </span>{p}
        </div>
      ))}
      <div style={{ fontSize: 14, lineHeight: 1.6, margin: "10px 0", padding: "8px 10px", borderRadius: 8, background: "rgba(128,128,128,0.08)", whiteSpace: "pre-line" }}>
        <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 2 }}>the answer{shown ? `, written by ${z.model}` : ""}</div>
        {z.answer}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {btn("Everything in it is in the passages", false)}
        {btn("Something in it isn't", true)}
      </div>
      {shown && (
        <div style={{ fontSize: 14, marginTop: 12, lineHeight: 1.6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ whiteSpace: "nowrap" }}>hunch: unsupported?</span>
            <div style={{ flex: "1 1 120px", height: 8, background: grey, borderRadius: 4 }}>
              <div className="hunch-bar" style={{ width: `${Math.round(z.p * 100)}%`, height: 8, borderRadius: 4, background: z.p >= 0.5 ? red : teal }} />
            </div>
            <b style={{ fontVariantNumeric: "tabular-nums" }}>{z.p.toFixed(2)}</b>
          </div>
          <div style={{ marginTop: 4 }}>
            {z.passed ? "Sure it's supported, so it goes straight to the user." : "Not sure it's supported, so a person sees it first."}{" "}
            The annotators {z.marked ? "marked something in it as unsupported." : "marked nothing in it."}
          </div>
          <p style={{ margin: "8px 0 0" }}>
            <b style={{ color: pick === z.marked ? teal : red }}>{pick === z.marked ? "You agree with the annotators." : "The annotators saw it the other way."}</b>{" "}
            {z.why}
          </p>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
        <button onClick={() => { setI((i + 1) % puzzles.length); setPick(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next answer →
        </button>
      </div>
    </div>
  );
};

export const ModelRanking = ({ models }) => {
  // models: [[name, answers, marked by the annotators, flagged by the question, passed at 0.7]]; bars to one scale
  const rows = [...models].sort((a, b) => b[2] - a[2]);
  const max = Math.max(...rows.map((m) => Math.max(m[2], m[3]) / m[1]));
  const gold = "#C9A227", teal = "#7D969B";
  const bar = (share, color) => (
    <div style={{ height: 10, marginTop: 3, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
      <div style={{ width: `${(100 * share) / max}%`, minWidth: 3, height: 10, background: color, borderRadius: 4 }} />
    </div>
  );
  const pct = (n, d) => `${Math.round((100 * n) / d)}%`;
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, opacity: 0.8 }}>
        <span><span style={{ color: gold }}>■</span> marked by the annotators</span>
        <span><span style={{ color: teal }}>■</span> flagged by the check</span>
      </div>
      {rows.map(([name, n, marked, flagged]) => (
        <div key={name} style={{ margin: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>{name}</span>
            <span style={{ fontVariantNumeric: "tabular-nums", opacity: 0.85 }}>{pct(marked, n)} · {pct(flagged, n)}</span>
          </div>
          {bar(marked / n, gold)}
          {bar(flagged / n, teal)}
        </div>
      ))}
    </div>
  );
};
