export const CommandPuzzle = ({ puzzles }) => {
  // puzzles: [{request, cwd, command, why, p: {question: p(yes)}, gold: {question: "yes"|"no"}}]; the guard stops at p(yes) >= 0.5
  const [i, setI] = useState(0);
  const [pick, setPick] = useState(null); // "run" or "stop"
  const z = puzzles[i];
  const shown = pick !== null;
  const QS = [["destroys", "destroys something hard to undo"], ["reaches_outside", "changes things outside the project"],
              ["sends_out", "sends something off the machine"]];
  const guard = QS.some(([q]) => z.p[q] >= 0.5) ? "stop" : "run";
  const panel = QS.some(([q]) => z.gold[q] === "yes") ? "stop" : "run";
  const say = (v) => (v === "stop" ? "stop it for a person" : "let it run");
  const teal = "#7D969B", red = "#C64D35", gold = "#C9A227", grey = "rgba(128,128,128,0.25)";
  const btn = (v, label) => (
    <button key={v} onClick={() => !shown && setPick(v)} disabled={shown}
      style={{ flex: 1, minWidth: 140, padding: "7px 10px", borderRadius: 8, fontSize: 14, color: "inherit", font: "inherit",
        border: `1px solid ${shown && v === panel ? teal : pick === v ? "currentColor" : grey}`, background: "transparent",
        cursor: shown ? "default" : "pointer" }}>
      {label}{shown && v === panel ? " ✓" : ""}{shown && pick === v ? <span style={{ opacity: 0.7, fontSize: 13 }}> · your pick</span> : ""}
    </button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Command {i + 1} of {puzzles.length}. The person asked:</div>
      <div style={{ fontSize: 15, fontStyle: "italic", margin: "4px 0 8px", overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>“{z.request}”</div>
      <div style={{ fontSize: 13, opacity: 0.7 }}>So the agent is about to run, in <code>{z.cwd}</code>:</div>
      <pre style={{ fontSize: 13, lineHeight: 1.5, margin: "6px 0 10px", padding: "8px 10px", borderRadius: 8, background: "rgba(128,128,128,0.1)",
        whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{z.command}</pre>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {btn("run", "Let it run")}
        {btn("stop", "Stop for a person")}
      </div>
      {shown && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, opacity: 0.7, marginBottom: 2 }}>The guard's three questions: how likely a yes, and the panel's answer</div>
          {QS.map(([q, what]) => {
            const p = z.p[q], yes = p >= 0.5;
            return (
              <div key={q} style={{ margin: "8px 0", fontSize: 13 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                  <span><code>{q}</code> <span style={{ opacity: 0.75 }}>{what}</span></span>
                  <span style={{ whiteSpace: "nowrap" }}>panel: <b style={{ color: z.gold[q] === "yes" ? gold : "inherit" }}>{z.gold[q] || "unsure"}</b></span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <span style={{ minWidth: 26, opacity: 0.8 }}>{yes ? "yes" : "no"}</span>
                  <div style={{ position: "relative", flex: 1, height: 6, background: grey, borderRadius: 3 }}>
                    <div className="hunch-bar" style={{ width: `${Math.round(p * 100)}%`, height: 6, borderRadius: 3, background: yes ? red : teal }} />
                    <div title="the bar: 0.5" style={{ position: "absolute", left: "50%", top: -3, width: 1, height: 12, background: "currentColor", opacity: 0.5 }} />
                  </div>
                  <span style={{ minWidth: 34, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{p.toFixed(2)}</span>
                </div>
              </div>
            );
          })}
          <p style={{ fontSize: 14, margin: "10px 0 0", lineHeight: 1.6 }}>
            <b style={{ color: pick === panel ? teal : red }}>{pick === panel ? "Agreed with the panel." : "The panel disagrees."}</b>{" "}
            The guard would {say(guard)}{guard === panel ? ", and the panel agrees." : `; the panel would ${say(panel)}.`} {z.why}
          </p>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, opacity: 0.6 }}>The tick marks 0.5: any yes past it stops the command.</span>
        <button onClick={() => { setI((i + 1) % puzzles.length); setPick(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next command →
        </button>
      </div>
    </div>
  );
};

export const GuardBars = ({ data }) => {
  // data: {total commands, bars: [[the bar a yes must clear, commands stopped, reviewed stops not needed, reviewed commands let through that needed a person]]}, one scale
  const bars = data.bars;
  const max = Math.max(...bars.flatMap((b) => [b[2], b[3]]));
  const teal = "#7D969B", red = "#C64D35";
  const bar = (n, color) => (
    <div style={{ flex: 1, height: 10, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
      <div style={{ width: `${(100 * n) / max}%`, minWidth: 3, height: 10, background: color, borderRadius: 4 }} />
    </div>
  );
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, opacity: 0.85 }}>
        <span><span style={{ color: teal }}>■</span> stops a person didn't need</span>
        <span><span style={{ color: red }}>■</span> harmful commands let through</span>
      </div>
      {bars.map(([b, stopped, needless, missed]) => (
        <div key={b} style={{ margin: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>Stop at <b>{b.toFixed(1)}</b></span><span style={{ opacity: 0.75 }}>{stopped} of {data.total.toLocaleString("en")} stopped</span>
          </div>
          {[[needless, teal], [missed, red]].map(([n, color]) => (
            <div key={color} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
              {bar(n, color)}
              <span style={{ minWidth: 24, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{n}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};
