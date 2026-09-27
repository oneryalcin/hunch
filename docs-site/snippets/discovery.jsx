export const ScopePuzzle = ({ items }) => {
  // items: [{doc, yes (the senior lawyer's call), why}], from the track's guidelines for the drilling request
  const [picks, setPicks] = useState({});
  const teal = "#7D969B", red = "#C64D35", grey = "rgba(128,128,128,0.25)";
  const done = Object.keys(picks).length === items.length;
  const agree = items.filter((it, k) => picks[k] === it.yes).length;
  const btn = (k, v, label) => (
    <button key={label} onClick={() => picks[k] === undefined && setPicks({ ...picks, [k]: v })} disabled={picks[k] !== undefined}
      style={{ border: `1px solid ${picks[k] === v ? "currentColor" : grey}`, borderRadius: 8, padding: "3px 12px", background: "transparent",
        color: "inherit", font: "inherit", fontSize: 13, cursor: picks[k] === undefined ? "pointer" : "default" }}>{label}</button>
  );
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <div style={{ fontSize: 13, opacity: 0.7 }}>Does the lawyer count it as relating to oil and gas drilling?</div>
      {items.map((it, k) => {
        const shown = picks[k] !== undefined;
        return (
          <div key={k} style={{ padding: "10px 0", borderTop: k ? `1px solid ${grey}` : "none" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 15, flex: "1 1 240px" }}>{it.doc}</span>
              <span style={{ display: "flex", gap: 6 }}>{btn(k, true, "Yes")}{btn(k, false, "No")}</span>
            </div>
            {shown && (
              <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                <b style={{ color: picks[k] === it.yes ? teal : red }}>{it.yes ? "Yes." : "No."}</b> {it.why}
              </div>
            )}
          </div>
        );
      })}
      {done && (
        <p style={{ fontSize: 14, margin: "10px 0 0" }}>
          You read it as the lawyer did on <b>{agree} of {items.length}</b>. The request's words don't say which way any of these go.
        </p>
      )}
    </div>
  );
};

export const FoundBars = ({ bars }) => {
  // bars: [[reader, % of the relevant messages found]], drawn to one scale (0-100%)
  return (
    <div className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {bars.map(([label, pct], k) => (
        <div key={label} style={{ margin: "10px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span>{label}</span><span><b>{Math.round(pct)}</b> in 100 found</span>
          </div>
          <div style={{ height: 14, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
            <div style={{ width: `${pct}%`, minWidth: 3, height: 14, borderRadius: 4,
              background: k === bars.length - 1 ? "#7D969B" : "rgba(128,128,128,0.55)" }} />
          </div>
        </div>
      ))}
    </div>
  );
};

export const ReadSlider = ({ data }) => {
  // data: {topic: {title, total, relevant, protocol: [% found after reading 0%, 0.5%, ... 100%], request: [...],
  //        keywords: [% read, % found]}}; the chart shows the first half of the collection
  const names = Object.keys(data);
  const [topic, setTopic] = useState(names[0]);
  const [step, setStep] = useState(24); // 12% read
  const d = data[topic];
  const teal = "#7D969B", red = "#C64D35", gold = "#C9A227", grey = "rgba(128,128,128,0.25)";
  const W = 320, H = 170, L = 8, B = 8, maxStep = 100; // x: 0-50% read
  const x = (i) => L + (i / maxStep) * (W - L - 4), yv = (v) => H - B - (v / 100) * (H - B - 6);
  const path = (arr) => arr.slice(0, maxStep + 1).map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${yv(v).toFixed(1)}`).join("");
  const kx = Math.min(d.keywords[0] * 2, maxStep);
  const read = step / 2, people = Math.round((d.total * read) / 100 / 1000) * 1000;
  return (
    <div className="not-prose hunch-widget" style={{ border: `1px solid ${grey}`, borderRadius: 12, padding: 16, margin: "16px 0" }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {names.map((n) => (
          <button key={n} onClick={() => setTopic(n)}
            style={{ border: `1px solid ${n === topic ? "currentColor" : grey}`, borderRadius: 8, padding: "4px 10px", background: "transparent",
              color: "inherit", font: "inherit", fontSize: 13, cursor: "pointer", opacity: n === topic ? 1 : 0.75 }}>{data[n].title}</button>
        ))}
      </div>
      <label style={{ display: "block", fontSize: 14 }}>
        A person reads the top <b>{read}%</b> of the collection, about {people.toLocaleString("en")} of {d.total.toLocaleString("en")} messages
        <input type="range" min={0} max={maxStep} value={step} onChange={(e) => setStep(+e.target.value)}
          aria-label="Share of the collection a person reads" style={{ width: "100%", accentColor: teal, marginTop: 6 }} />
      </label>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 560, display: "block", margin: "4px 0" }} role="img"
        aria-label={`Reading the top ${read}% finds ${d.protocol[step]}% with the lawyer's reading, ${d.request[step]}% with the request's words`}>
        <line x1={L} y1={yv(0)} x2={W - 4} y2={yv(0)} stroke={grey} />
        <line x1={L} y1={yv(80)} x2={W - 4} y2={yv(80)} stroke={grey} strokeDasharray="3 3" />
        <path d={path(d.request)} fill="none" stroke="rgba(128,128,128,0.6)" strokeWidth={2} />
        <path d={path(d.protocol)} fill="none" stroke={teal} strokeWidth={2.5} />
        <circle cx={x(kx)} cy={yv(d.keywords[1])} r={4.5} fill={red} />
        <line x1={x(step)} y1={yv(0)} x2={x(step)} y2={yv(100)} stroke={gold} strokeWidth={1.5} />
      </svg>
      <div style={{ fontSize: 12, opacity: 0.7, display: "flex", justifyContent: "space-between" }}><span>read 0%</span><span>dashed line: 80% found</span><span>50%</span></div>
      <div style={{ fontSize: 14, marginTop: 10, lineHeight: 1.7 }}>
        <div><span style={{ color: teal }}>━</span> With the lawyer's reading: <b>{d.protocol[step]}%</b> of what the lawyer wanted is in front of a person</div>
        <div><span style={{ opacity: 0.6 }}>━</span> With the request's words: <b>{d.request[step]}%</b></div>
        <div><span style={{ color: red }}>●</span> Keyword search reads {d.keywords[0]}% and finds {d.keywords[1]}%</div>
      </div>
    </div>
  );
};
