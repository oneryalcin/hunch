export const PairPuzzle = ({ puzzles }) => {
  // puzzles: [{product, why, candidates: [[name, p(same) from the question, is the match (0/1)]]}], rules' order
  const [i, setI] = useState(0);
  const [pick, setPick] = useState(null); // index of a candidate, or -1 for "none of these"
  const z = puzzles[i];
  const match = z.candidates.findIndex((c) => c[2]);
  const shown = pick !== null;
  const hunchPick = z.candidates.reduce((best, c, k) => (c[1] >= 0.5 && (best < 0 || c[1] > z.candidates[best][1]) ? k : best), -1);
  const right = pick === match;
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const row = (name, k) => {
    const c = k >= 0 ? z.candidates[k] : null;
    const isMatch = k === match;
    return (
      <button key={k} onClick={() => !shown && setPick(k)} disabled={shown}
        style={{ display: "block", width: "100%", textAlign: "left", padding: "7px 10px", margin: "5px 0", borderRadius: 8,
          border: `1px solid ${shown && isMatch ? teal : pick === k ? "currentColor" : grey}`, background: "transparent",
          cursor: shown ? "default" : "pointer", color: "inherit", font: "inherit", fontSize: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
          <span>{name}{shown && isMatch ? " ✓" : ""}</span>
          <span style={{ opacity: 0.7, fontSize: 13 }}>
            {shown && k === 0 ? "the rules' pick · " : ""}{shown && pick === k ? "your pick" : ""}
          </span>
        </div>
        {shown && c && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, fontSize: 12, opacity: 0.9 }}>
            <span style={{ minWidth: 64, whiteSpace: "nowrap", opacity: 0.8 }}>same? {c[1] >= 0.5 ? "yes" : "no"}</span>
            <div style={{ flex: 1, height: 6, background: grey, borderRadius: 3 }}>
              <div className="hunch-bar" style={{ width: `${Math.round(c[1] * 100)}%`, height: 6, borderRadius: 3, background: c[1] >= 0.5 ? teal : "rgba(128,128,128,0.55)" }} />
            </div>
            <span style={{ minWidth: 34, textAlign: "right", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>{c[1].toFixed(2)}</span>
          </div>
        )}
      </button>
    );
  };
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Product {i + 1} of {puzzles.length}. Which of these is the same product?</div>
      <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0 8px" }}>{z.product}</div>
      {z.candidates.map((c, k) => row(c[0], k))}
      {row("None of these", -1)}
      {shown && (
        <p style={{ fontSize: 14, margin: "10px 0 0", lineHeight: 1.6 }}>
          <b style={{ color: right ? teal : red }}>{right ? "Right." : "Not quite."}</b>{" "}
          The rules picked the first one{match === 0 ? ", and that's right" : ", and that's wrong"}.{" "}
          The question said {hunchPick < 0 ? "none of them" : `yes to “${z.candidates[hunchPick][0]}”`}
          {hunchPick === match ? ", and that's right." : "."} {z.why}
        </p>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
        <button onClick={() => { setI((i + 1) % puzzles.length); setPick(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next product →
        </button>
      </div>
    </div>
  );
};

export const WrongMatches = ({ bars }) => {
  // bars: [[label, wrong matches, matches made]], drawn to one scale
  const max = Math.max(...bars.map((b) => b[1]));
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {bars.map(([label, wrong, made]) => (
        <div key={label} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span>{label}</span><span><b>{wrong}</b> wrong of {made.toLocaleString("en")} matches</span>
          </div>
          <div style={{ height: 14, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${(100 * wrong) / max}%`, minWidth: 3, height: 14, background: "#C64D35", borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
};

export const PairFunnel = ({ steps }) => {
  // steps: [[count, what it is]], largest first; bar widths to one scale, with a sliver for the smallest
  const max = steps[0][0];
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {steps.map(([n, what]) => (
        <div key={what} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>{what}</span><b style={{ fontVariantNumeric: "tabular-nums" }}>{n.toLocaleString("en")}</b>
          </div>
          <div style={{ height: 12, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${(100 * n) / max}%`, minWidth: 3, height: 12, background: "#7D969B", borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
};
