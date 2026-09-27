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

export const PairSieve = ({ data }) => {
  // data: {product, buy: [[name, shares a word, shares the code, word overlap, rank if kept, p(same) if kept, match]]}
  const STEPS = [
    `All ${data.buy.length.toLocaleString("en")} Buy products, each one a possible match for the turntable.`,
    `${(data.buy.length - data.buy.filter((d) => d[1]).length).toLocaleString("en")} share no word with it. They go.`,
    `The ${data.buy.filter((d) => d[1]).length} left are sorted: a shared model code first, then the most words in common.`,
    "The best five are kept. Everything else goes.",
    "Now the question looks at each of the five. Only one is the same product.",
  ];
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    if (!playing) return;
    if (step >= STEPS.length - 1) { setPlaying(false); return; }
    const t = setTimeout(() => setStep(step + 1), step === 0 ? 1200 : 2400);
    return () => clearTimeout(t);
  }, [playing, step]);
  const play = () => {
    const reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setStep(STEPS.length - 1); return; }
    setStep(0); setPlaying(true);
  };
  const cols = 42, gap = 9, W = 14 + cols * gap + 40, H = 26 + Math.ceil(data.buy.length / cols) * gap + 6;
  const ranked = data.buy.map((d, i) => [d, i]).filter(([d]) => d[1])
    .sort((a, b) => b[0][2] - a[0][2] || b[0][3] - a[0][3] || a[1] - b[1]).map(([, i]) => i);
  const place = {};
  ranked.forEach((i, k) => { place[i] = k; });
  const teal = "#7D969B", gold = "#C9A227", grey = "rgba(128,128,128,0.25)";
  const pos = (d, i) => {
    if (step >= 3 && d[4]) return [W - 22, 30 + (d[4] - 1) * ((H - 50) / 4)];   // the five, in a column
    if (step >= 2 && d[1]) { const k = place[i]; return [14 + (k % 14) * 12, 30 + Math.floor(k / 14) * 12]; }
    return [14 + (i % cols) * gap, 26 + Math.floor(i / cols) * gap];            // everyone, in a grid
  };
  const alpha = (d) => (step >= 3 ? (d[4] ? 1 : 0.06) : step >= 1 ? (d[1] ? 1 : 0.08) : 1);
  const five = data.buy.filter((d) => d[4]).sort((a, b) => a[4] - b[4]);
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <style>{`.hunch-sieve circle{transition:transform .9s cubic-bezier(.4,0,.2,1),opacity .6s} .hunch-fade{transition:opacity .6s,max-height .6s}
        @media (prefers-reduced-motion: reduce){.hunch-sieve circle,.hunch-fade{transition:none}}`}</style>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 14 }}>
        <span><b>{data.product}</b> · step {step + 1} of {STEPS.length}</span>
        <button onClick={play} disabled={playing}
          style={{ border: `1px solid ${grey}`, borderRadius: 8, padding: "5px 12px", background: "transparent", color: "inherit", font: "inherit", cursor: playing ? "default" : "pointer" }}>
          {playing ? "Playing…" : step === 0 ? "▶ Play" : "↺ Replay"}
        </button>
      </div>
      <svg className="hunch-sieve" viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 560, display: "block", margin: "8px 0" }}
        role="img" aria-label={STEPS[step]}>
        {data.buy.map((d, i) => {
          const [x, y] = pos(d, i);
          return (
            <circle key={i} r={step >= 3 && d[4] ? 6 : 3.4} cx={0} cy={0}
              style={{ transform: `translate(${x}px, ${y}px)`, opacity: alpha(d) }}
              fill={d[2] && step >= 2 ? gold : teal}
              onMouseEnter={() => setHover(d[0])} onMouseLeave={() => setHover(null)}>
              <title>{d[0]}</title>
            </circle>
          );
        })}
      </svg>
      <p style={{ fontSize: 15, margin: "4px 0 0", minHeight: 24 }}>{STEPS[step]}</p>
      <div className="hunch-fade" style={{ opacity: step >= 3 ? 1 : 0, maxHeight: step >= 3 ? 400 : 0, overflow: "hidden" }}>
        {five.map((d) => (
          <div key={d[0]} style={{ margin: "8px 0", fontSize: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span><span style={{ color: d[2] ? gold : teal }}>●</span> {d[0]}{step >= 4 && d[6] ? " ✓" : ""}</span>
              {step >= 4 && <span style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums", opacity: 0.85 }}>{d[5] >= 0.5 ? "same" : "no"} {d[5].toFixed(2)}</span>}
            </div>
            <div className="hunch-fade" style={{ height: 5, marginTop: 4, background: grey, borderRadius: 3, opacity: step >= 4 ? 1 : 0 }}>
              <div style={{ width: `${Math.round(Math.max(d[5], 0) * 100)}%`, height: 5, borderRadius: 3, background: d[5] >= 0.5 ? teal : "rgba(128,128,128,0.6)" }} />
            </div>
          </div>
        ))}
      </div>
      <p style={{ fontSize: 13, margin: "6px 0 0", opacity: 0.7, minHeight: 20 }}>
        {hover ? hover : <><span style={{ color: gold }}>●</span> shares the model code · point at a dot for its name</>}
      </p>
    </div>
  );
};
