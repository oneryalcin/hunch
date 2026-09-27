export const PaperPuzzle = ({ papers }) => {
  // papers: [{title, kept (screeners), inReview, p (hunch's p(yes)), why}], from the Wilson disease review
  const [picks, setPicks] = useState({});
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const btn = (k, v, label) => (
    <button key={label} onClick={() => picks[k] === undefined && setPicks({ ...picks, [k]: v })} disabled={picks[k] !== undefined}
      style={{ border: `1px solid ${picks[k] === v ? "currentColor" : grey}`, borderRadius: 8, padding: "3px 12px", background: "transparent",
        color: "inherit", font: "inherit", fontSize: 13, cursor: picks[k] === undefined ? "pointer" : "default" }}>{label}</button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Would you read the full paper? You see the title; hunch also read the abstract, where there was one, and the publication type.</div>
      {papers.map((it, k) => {
        const shown = picks[k] !== undefined;
        return (
          <div key={k} style={{ padding: "10px 0", borderTop: k ? `1px solid ${grey}` : "none" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 15, flex: "1 1 240px" }}>{it.title}</span>
              <span style={{ display: "flex", gap: 6 }}>{btn(k, true, "Read it")}{btn(k, false, "Skip")}</span>
            </div>
            {shown && (
              <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 3 }}>
                  <span>Screeners: <b>{it.kept ? "read it" : "skipped"}</b></span>
                  <span>In the review: <b style={{ color: it.inReview ? teal : "inherit" }}>{it.inReview ? "yes" : "no"}</b></span>
                  <span>hunch: <b>p(yes) {it.p.toFixed(2)}</b></span>
                </div>
                <span style={{ opacity: 0.85 }}>{it.why}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export const FoundCurve = ({ data }) => {
  // data: {review: {title, papers, inReview, hunch: [% of the review's papers found after reading 0%, 0.5%, ... 100%],
  //        active: [...]}}
  const names = Object.keys(data);
  const [review, setReview] = useState(names[0]);
  const [step, setStep] = useState(80); // 40% read
  const d = data[review];
  const teal = "#7D969B", gold = "#C9A227", grey = "rgba(128,128,128,0.25)";
  const W = 320, H = 170, L = 8, B = 8, maxStep = 200;
  const x = (i) => L + (i / maxStep) * (W - L - 4), yv = (v) => H - B - (v / 100) * (H - B - 6);
  const path = (arr) => arr.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${yv(v).toFixed(1)}`).join("");
  const read = step / 2, n = Math.round((d.papers * read) / 100);
  const by = (arr, pct) => arr.findIndex((v) => v >= pct) / 2;
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {names.map((k) => (
          <button key={k} onClick={() => setReview(k)}
            style={{ border: `1px solid ${k === review ? "currentColor" : grey}`, borderRadius: 8, padding: "4px 10px", background: "transparent",
              color: "inherit", font: "inherit", fontSize: 13, cursor: "pointer", opacity: k === review ? 1 : 0.75 }}>{data[k].title}</button>
        ))}
      </div>
      <label style={{ display: "block", fontSize: 14 }}>
        Read the first <b>{read}%</b> of the pile: {n.toLocaleString("en")} of {d.papers.toLocaleString("en")} papers
        <input type="range" min={0} max={maxStep} value={step} onChange={(e) => setStep(+e.target.value)}
          aria-label="Share of the pile read" style={{ width: "100%", accentColor: teal, marginTop: 6 }} />
      </label>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 560, display: "block", margin: "4px 0" }} role="img"
        aria-label={`After reading ${read}%, hunch's order has found ${d.hunch[step]}% of the review's papers, active learning ${d.active[step]}%`}>
        <line x1={L} y1={yv(0)} x2={W - 4} y2={yv(0)} stroke={grey} />
        <line x1={L} y1={yv(100)} x2={W - 4} y2={yv(100)} stroke={grey} strokeDasharray="3 3" />
        <path d={path(d.active)} fill="none" stroke="rgba(128,128,128,0.6)" strokeWidth={2} />
        <path d={path(d.hunch)} fill="none" stroke={teal} strokeWidth={2.5} />
        <line x1={x(step)} y1={yv(0)} x2={x(step)} y2={yv(100)} stroke={gold} strokeWidth={1.5} />
      </svg>
      <div style={{ fontSize: 12, opacity: 0.7, display: "flex", justifyContent: "space-between" }}><span>read 0%</span><span>dashed line: all {d.inReview} found</span><span>100%</span></div>
      <div style={{ fontSize: 14, marginTop: 10, lineHeight: 1.7 }}>
        <div><span style={{ color: teal }}>━</span> In hunch's order: <b>{d.hunch[step]}%</b> of the {d.inReview} papers in the review found; 95% of them by {by(d.hunch, 95)}%, all by {by(d.hunch, 100)}%</div>
        <div><span style={{ opacity: 0.6 }}>━</span> Active learning: <b>{d.active[step]}%</b>; 95% by {by(d.active, 95)}%, all by {by(d.active, 100)}%</div>
      </div>
    </div>
  );
};
