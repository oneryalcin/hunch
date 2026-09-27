export const ClausePuzzle = ({ puzzles }) => {
  // puzzles: [{id, chars, excerpt, elsewhere, why, gold, hunch: [label, {label: p}], first: [label, {label: p}]}]
  const [i, setI] = useState(0);
  const [pick, setPick] = useState(null);
  const z = puzzles[i];
  const shown = pick !== null;
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const OPTS = [
    ["says_so", "Yes, it must be marked"],
    ["says_otherwise", "No, unmarked counts too"],
    ["not_mentioned", "It doesn't say"],
  ];
  const name = (l) => OPTS.find((o) => o[0] === l)[1].toLowerCase();
  const [label, probs] = z.hunch;
  const btn = { border: `1px solid ${grey}`, borderRadius: 8, padding: "7px 10px", background: "transparent",
    color: "inherit", font: "inherit", fontSize: 14, textAlign: "left" };
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-bar{transition:width .4s ease-out} @media (prefers-reduced-motion: reduce){.hunch-bar{transition:none}}`}</style>
      <div style={{ fontSize: 13, opacity: 0.7 }}>
        Contract {i + 1} of {puzzles.length}, {z.chars.toLocaleString("en")} characters long. The sentence that matters:
      </div>
      <blockquote style={{ margin: "8px 0", padding: "8px 12px", borderLeft: `3px solid ${teal}`, fontSize: 15, lineHeight: 1.6, overflowWrap: "anywhere" }}>
        {z.excerpt}
      </blockquote>
      {z.elsewhere === 0 && <div style={{ fontSize: 13, opacity: 0.7 }}>Nowhere else does it mention marking, labels or designations.</div>}
      <div style={{ fontSize: 15, fontWeight: 600, margin: "10px 0 6px" }}>Must information be marked confidential to count?</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {OPTS.map(([l, text]) => (
          <button key={l} onClick={() => !shown && setPick(l)} disabled={shown}
            style={{ ...btn, flex: "1 1 160px", cursor: shown ? "default" : "pointer",
              borderColor: shown && l === z.gold ? teal : pick === l ? "currentColor" : grey }}>
            {text}{shown && l === z.gold ? " ✓" : ""}
            {shown && pick === l && <span style={{ opacity: 0.7, fontSize: 13 }}> · your answer</span>}
          </button>
        ))}
      </div>
      {shown && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 2 }}>hunch, and how sure it was of each answer:</div>
          {OPTS.map(([l, text]) => (
            <div key={l} style={{ display: "flex", alignItems: "center", gap: 8, margin: "4px 0", fontSize: 13 }}>
              <span style={{ width: 132, flexShrink: 0, fontWeight: l === label ? 600 : 400 }}>{text}</span>
              <div style={{ flex: 1, height: 6, background: grey, borderRadius: 3 }}>
                <div className="hunch-bar" style={{ width: `${Math.round(probs[l] * 100)}%`, height: 6, borderRadius: 3,
                  background: l !== label ? "rgba(128,128,128,0.55)" : label === z.gold ? teal : red }} />
              </div>
              <span style={{ minWidth: 34, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{probs[l].toFixed(2)}</span>
            </div>
          ))}
          <p style={{ fontSize: 14, margin: "10px 0 0", lineHeight: 1.6 }}>
            <b style={{ color: pick === z.gold ? teal : red }}>{pick === z.gold ? "You agree with the lawyers." : "Your answer isn't the lawyers'."}</b>{" "}
            They labelled it “{name(z.gold)}”. hunch said “{name(label)}”{label === z.gold ? ", the same as them." : ", which is not."}{" "}
            {z.first[0] !== label ? `The first wording of the question said “${name(z.first[0])}”. ` : ""}{z.why}
          </p>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
        <button onClick={() => { setI((i + 1) % puzzles.length); setPick(null); }} style={{ ...btn, padding: "6px 12px", cursor: "pointer" }}>
          Next contract →
        </button>
      </div>
    </div>
  );
};

export const FixDots = ({ fix }) => {
  // fix: [[first wording's answer, reworded answer]] on contracts the lawyers labelled not_mentioned
  const teal = "#7D969B", red = "#C64D35", gold = "#C9A227";
  const color = { not_mentioned: teal, says_otherwise: red, says_so: gold };
  const rows = [["First wording", 0], ["Reworded", 1]];
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {rows.map(([title, k]) => {
        const wrong = fix.filter((f) => f[k] === "says_otherwise").length;
        return (
          <div key={title} style={{ margin: "10px 0" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
              <span>{title}</span><span><b>{wrong}</b> of {fix.length} read as “unmarked counts too”</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }} role="img"
              aria-label={`${title}: ${wrong} of ${fix.length} contracts answered "unmarked counts too"`}>
              {fix.map((f, i) => (
                <span key={i} style={{ width: 12, height: 12, borderRadius: 6, background: color[f[k]] }} />
              ))}
            </div>
          </div>
        );
      })}
      <div style={{ fontSize: 13, opacity: 0.75, marginTop: 6 }}>
        <span style={{ color: teal }}>●</span> doesn't say (the lawyers' label) · <span style={{ color: red }}>●</span> unmarked counts too
        · <span style={{ color: gold }}>●</span> must be marked
      </div>
    </div>
  );
};

export const ClauseBars = ({ clauses }) => {
  // clauses: [[question, right with the first wording, right reworded, contracts]], drawn to one scale (0 to 100%)
  const NAMES = { must_be_marked: "Must it be marked?", advisors_allowed: "May advisors see it?", copies_allowed: "May it be copied?",
    may_keep_after_return: "May a copy be kept after return?", survives_termination: "Do duties outlive the agreement?" };
  const teal = "#7D969B";
  const bar = (n, of, bg) => (
    <div style={{ height: 10, marginTop: 3, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
      <div style={{ width: `${(100 * n) / of}%`, height: 10, background: bg, borderRadius: 4 }} />
    </div>
  );
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {clauses.map(([q, first, now, of]) => (
        <div key={q} style={{ margin: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>{NAMES[q]}</span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}><b>{now}</b> of {of} right <span style={{ opacity: 0.7 }}>(was {first})</span></span>
          </div>
          {bar(first, of, "rgba(128,128,128,0.45)")}
          {bar(now, of, teal)}
        </div>
      ))}
      <div style={{ fontSize: 13, opacity: 0.75 }}>
        <span style={{ color: "rgba(128,128,128,0.8)" }}>■</span> first wording · <span style={{ color: teal }}>■</span> reworded
      </div>
    </div>
  );
};
