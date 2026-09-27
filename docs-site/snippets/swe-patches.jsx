export const PatchGuess = ({ puzzles }) => {
  // puzzles: [{issue, says, patch: [lines], cut, passed (0/1), p: p(passes) from the question, claims, why}]
  const [i, setI] = useState(0);
  const [guess, setGuess] = useState(null); // 1 = passes, 0 = fails
  const z = puzzles[i];
  const shown = guess !== null;
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const code = (text) => text.split("`").map((s, k) => (k % 2 ? <code key={k} style={{ fontSize: "0.9em" }}>{s}</code> : s));
  const sure = z.p <= 0.2; // act: {no: 0.80}: confident enough in "fails" to skip the tests
  const btn = (label, v) => (
    <button key={v} onClick={() => !shown && setGuess(v)} disabled={shown}
      style={{ flex: "1 1 140px", padding: "7px 10px", borderRadius: 8, font: "inherit", fontSize: 14, color: "inherit",
        background: "transparent", cursor: shown ? "default" : "pointer",
        border: `1px solid ${shown && z.passed === v ? teal : guess === v ? "currentColor" : grey}` }}>
      {label}{shown && z.passed === v ? " ✓" : ""}{shown && guess === v ? " · your guess" : ""}
    </button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0", minWidth: 0 }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Run {i + 1} of {puzzles.length}. The agent says it fixed the issue. Did the tests pass?</div>
      <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0 10px" }}>Issue: {code(z.issue)}</div>
      <div style={{ fontSize: 13, opacity: 0.7 }}>The agent's last words</div>
      <p style={{ fontSize: 14, lineHeight: 1.55, margin: "4px 0 10px", paddingLeft: 10, borderLeft: `3px solid ${grey}` }}>{code(z.says)}</p>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Its patch{z.cut ? `, trimmed (${z.cut} more line${z.cut > 1 ? "s" : ""})` : ""}</div>
      <pre style={{ fontSize: 12, lineHeight: 1.45, margin: "4px 0 10px", padding: "8px 10px", borderRadius: 8, overflowX: "auto",
        maxWidth: "100%", background: "rgba(128,128,128,0.08)", whiteSpace: "pre" }}>
        {z.patch.map((ln, k) => (
          <div key={k} style={{ color: ln[0] === "+" ? teal : ln[0] === "-" ? red : "inherit", opacity: ln.startsWith("file:") || ln.startsWith("@@") ? 0.6 : 1 }}>{ln || " "}</div>
        ))}
      </pre>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{btn("Passes the tests", 1)}{btn("Fails", 0)}</div>
      {shown && (
        <div style={{ marginTop: 12, fontSize: 14, lineHeight: 1.6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span style={{ whiteSpace: "nowrap", opacity: 0.8 }}>hunch: p(passes)</span>
            <div style={{ flex: 1, height: 6, background: grey, borderRadius: 3 }}>
              <div className="hunch-bar" style={{ width: `${Math.round(z.p * 100)}%`, height: 6, borderRadius: 3, background: z.p >= 0.5 ? teal : "rgba(128,128,128,0.55)" }} />
            </div>
            <span style={{ minWidth: 34, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{z.p.toFixed(2)}</span>
          </div>
          <p style={{ margin: "8px 0 0" }}>
            <b style={{ color: guess === z.passed ? teal : red }}>{guess === z.passed ? "Right." : "Not quite."}</b>{" "}
            The tests {z.passed ? "passed" : "failed"}.{" "}
            {sure ? "hunch was sure enough it fails to set it aside without running them." : "hunch wasn't sure enough to skip the tests, so they run."}{" "}
            {code(z.why)}
          </p>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
        <button onClick={() => { setI((i + 1) % puzzles.length); setGuess(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next run →
        </button>
      </div>
    </div>
  );
};

export const PassByP = ({ bars }) => {
  // bars: [[hunch's p(passes) range, runs, share that passed (weighted to the real pass rate), hunch's average p]]
  // one scale, 0 to 100%: the bar is what happened, the tick is what hunch said
  const teal = "#7D969B";
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {bars.map(([label, n, passed, p]) => (
        <div key={label} style={{ margin: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>p(passes) {label} <span style={{ opacity: 0.6 }}>· {n} runs</span></span>
            <span><b>{Math.round(passed * 100)}%</b> passed</span>
          </div>
          <div style={{ position: "relative", height: 14, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${passed * 100}%`, minWidth: 3, height: 14, background: teal, borderRadius: 4 }} />
            <div title={`hunch's average p(passes): ${p.toFixed(2)}`}
              style={{ position: "absolute", left: `calc(${p * 100}% - 1px)`, top: -3, width: 2, height: 20, background: "currentColor" }} />
          </div>
        </div>
      ))}
      <div style={{ fontSize: 13, opacity: 0.7, marginTop: 6 }}>
        Bar: share of runs that passed, weighted to the real pass rate. <span style={{ display: "inline-block", width: 2, height: 11, background: "currentColor", verticalAlign: "-1px" }} /> hunch's average p(passes).
      </div>
    </div>
  );
};
