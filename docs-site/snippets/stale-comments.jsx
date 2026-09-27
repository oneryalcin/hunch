export const StalePuzzle = ({ puzzles, views }) => {
  // puzzles: [{kind, comment, diff: [[op, line]], facts, gold: yes|no, p: [p(stale) per view], sure, why}]
  // views: [{label}], the last is the shipped spec
  const [i, setI] = useState(0);
  const [guess, setGuess] = useState(null); // "yes" (stale) or "no" (still fine)
  const z = puzzles[i];
  const shown = guess !== null;
  const p = z.p[z.p.length - 1];
  const said = p >= 0.5 ? "yes" : "no";
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const KIND = { summary: "the summary sentence", param: "a @param line", return: "a @return line" };
  const word = (x) => (x === "yes" ? "stale" : "still fine");
  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace";
  const choice = (x, label) => (
    <button key={x} onClick={() => !shown && setGuess(x)} disabled={shown}
      style={{ flex: 1, minWidth: 120, padding: "7px 10px", borderRadius: 8, font: "inherit", fontSize: 14, color: "inherit",
        background: "transparent", cursor: shown ? "default" : "pointer",
        border: `1px solid ${shown && x === z.gold ? teal : guess === x ? "currentColor" : grey}` }}>
      {label}{shown && x === z.gold ? " ✓" : ""}{shown && guess === x ? " · your guess" : ""}
    </button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Comment {i + 1} of {puzzles.length}: {KIND[z.kind]}. After this commit, is it still true?</div>
      <div style={{ fontFamily: mono, fontSize: 13.5, margin: "6px 0 8px", padding: "6px 10px", borderLeft: `3px solid ${grey}`, overflowWrap: "anywhere" }}>
        {z.comment}
      </div>
      <pre style={{ margin: 0, padding: "8px 0", fontFamily: mono, fontSize: 12.5, lineHeight: 1.5, whiteSpace: "pre-wrap",
        overflowWrap: "anywhere", border: `1px solid ${grey}`, borderRadius: 8, background: "transparent" }}>
        {z.diff.map(([op, line], k) => (
          <div key={k} style={{ padding: "0 10px 0 26px", textIndent: -16,
            background: op === "-" ? "rgba(198,77,53,0.12)" : op === "+" ? "rgba(125,150,155,0.18)" : "transparent" }}>
            <span style={{ opacity: 0.6, display: "inline-block", width: 16, textIndent: 0 }}>{op === " " ? "" : op === "-" ? "−" : "+"}</span>{line || " "}
          </div>
        ))}
      </pre>
      <div style={{ fontSize: 12, opacity: 0.65, margin: "4px 0 8px" }}>− before the commit · + after it</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {choice("no", "Still fine")}
        {choice("yes", "Now stale")}
      </div>
      {shown && (
        <div style={{ fontSize: 14, marginTop: 12, lineHeight: 1.6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ whiteSpace: "nowrap" }}>hunch: <b>{word(said)}</b></span>
            <div style={{ flex: 1, minWidth: 80, height: 6, background: grey, borderRadius: 3 }}>
              <div className="hunch-bar" style={{ width: `${Math.round(p * 100)}%`, height: 6, borderRadius: 3, background: said === "yes" ? teal : "rgba(128,128,128,0.55)" }} />
            </div>
            <span style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>p(stale) {p.toFixed(2)}</span>
          </div>
          <div style={{ fontSize: 13, opacity: 0.8 }}>
            {said === "yes" ? (z.sure ? "Sure enough to fail the check." : "Not sure enough: listed for a person.") : "The check stays quiet."}{" "}
            The parser found: {z.facts}.
          </div>
          <p style={{ margin: "8px 0 0" }}>
            <b style={{ color: guess === z.gold ? teal : red }}>{guess === z.gold ? "Right." : "Not quite."}</b>{" "}
            Checked by hand: {word(z.gold)}{said === z.gold ? ", and hunch agrees." : ". hunch said the opposite."} {z.why}
          </p>
          <div style={{ fontSize: 13, opacity: 0.8, marginTop: 6 }}>
            p(stale) as the model saw more:{" "}
            {views.map((v, k) => (
              <span key={k} style={{ whiteSpace: "nowrap" }}>{k ? " → " : ""}<span style={{ fontVariantNumeric: "tabular-nums" }}>{z.p[k].toFixed(2)}</span></span>
            ))}
            <span style={{ opacity: 0.8 }}> (code now; before and after; with the parser's facts)</span>
          </div>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
        <button onClick={() => { setI((i + 1) % puzzles.length); setGuess(null); }}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "6px 12px", background: "transparent", color: "inherit", font: "inherit", fontSize: 14, cursor: "pointer" }}>
          Next comment →
        </button>
      </div>
    </div>
  );
};

export const WrongByView = ({ views }) => {
  // views: [{label, wrong, of, by_kind: {summary, param, return}}], drawn to one scale
  const max = Math.max(...views.map((v) => v.wrong));
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Wrong answers on the {views[0].of} hand-checked comments</div>
      {views.map((v) => (
        <div key={v.label} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>{v.label}</span><span><b>{v.wrong}</b> wrong</span>
          </div>
          <div style={{ height: 14, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${(100 * v.wrong) / max}%`, minWidth: 3, height: 14, background: "#C64D35", borderRadius: 4 }} />
          </div>
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 3, fontVariantNumeric: "tabular-nums" }}>
            summaries {v.by_kind.summary} · @param {v.by_kind.param} · @return {v.by_kind.return}
          </div>
        </div>
      ))}
    </div>
  );
};
