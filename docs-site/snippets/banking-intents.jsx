export const IntentPuzzle = ({ puzzles }) => {
  // puzzles: [{text, labels: [[label, description, reviewers of 3 who accepted it, is the answer key's (0/1)]],
  //            model: the model's label, p: its confidence, verdict: "model" | "key" | "both"}]
  const [i, setI] = useState(0);
  const [pick, setPick] = useState(null); // 0 or 1: a label; 2: both fit
  const [score, setScore] = useState([0, 0]); // [agreed with the panel, answered]
  const z = puzzles[i];
  const shown = pick !== null;
  const right = z.verdict === "both" ? 2 : z.labels.findIndex((l) => (z.verdict === "key") === !!l[3]);
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const choose = (k) => {
    if (shown) return;
    setPick(k);
    setScore([score[0] + (k === right ? 1 : 0), score[1] + 1]);
  };
  const button = (k, children) => (
    <button key={k} onClick={() => choose(k)} disabled={shown}
      style={{ display: "block", width: "100%", textAlign: "left", padding: "8px 10px", margin: "6px 0", borderRadius: 8,
        border: `1px solid ${shown && (k === right || (right === 2 && k < 2)) ? teal : pick === k ? "currentColor" : grey}`,
        background: "transparent", cursor: shown ? "default" : "pointer", color: "inherit", font: "inherit", fontSize: 14,
        overflowWrap: "anywhere" }}>
      {children}
    </button>
  );
  const verdict = { model: "the model was right and the answer key wrong.", key: "the answer key was right and the model wrong.",
    both: "both labels fit this message." }[z.verdict];
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Message {i + 1} of {puzzles.length}. Which label is right?</div>
      <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0 8px", overflowWrap: "anywhere" }}>“{z.text}”</div>
      {z.labels.map(([label, desc, votes, isKey], k) => button(k, (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <code style={{ fontSize: 13 }}>{label}</code>
            {shown && (
              <span style={{ fontSize: 13, opacity: 0.85 }}>
                {isKey ? "the answer key" : `the model, ${z.p.toFixed(2)} sure`}{pick === k ? " · your pick" : ""}
              </span>
            )}
          </div>
          <div style={{ fontSize: 13, opacity: 0.8, marginTop: 2 }}>{desc}</div>
          {shown && <div style={{ fontSize: 12, marginTop: 4, color: votes >= 2 ? teal : red }}>{votes} of 3 reviewers accepted it</div>}
        </>
      )))}
      {button(2, <span>Both fit{shown && pick === 2 ? " · your pick" : ""}</span>)}
      {shown && (
        <p style={{ fontSize: 14, margin: "10px 0 0", lineHeight: 1.6 }}>
          <b style={{ color: pick === right ? teal : red }}>{pick === right ? "The panel agrees." : "The panel saw it differently."}</b>{" "}
          Three reviewers who didn't know which label was whose decided that {verdict}
        </p>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        <span style={{ fontSize: 13, opacity: 0.7 }}>{score[1] ? `You agreed with the panel on ${score[0]} of ${score[1]}.` : ""}</span>
        <button onClick={() => { setI((i + 1) % puzzles.length); setPick(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next message →
        </button>
      </div>
    </div>
  );
};

export const DisputeSplit = ({ split }) => {
  // split: [[what the panel decided, rows]], drawn to one scale
  const total = split.reduce((s, b) => s + b[1], 0);
  const colors = ["#7D969B", "rgba(128,128,128,0.55)", "#C64D35"];
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {split.map(([label, n], k) => (
        <div key={label} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span>{label}</span><span style={{ whiteSpace: "nowrap" }}><b>{n}</b> of {total}</span>
          </div>
          <div style={{ height: 14, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${(100 * n) / total}%`, minWidth: 3, height: 14, background: colors[k], borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
};
