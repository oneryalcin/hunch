export const RunBars = ({ groups }) => {
  // groups: [[what is measured, [[label, value, shown as]]]]; each group's bars share one scale, and a bar thinner
  // than a pixel is drawn at one pixel. Static: no state, no motion.
  const teal = "#7D969B";
  return (
    <figure className="not-prose" style={{ margin: "16px 0", fontSize: 14 }}>
      {groups.map(([what, bars]) => {
        const max = Math.max(...bars.map((b) => b[1]));
        return (
          <div key={what} style={{ margin: "14px 0" }}>
            <div style={{ fontSize: 13, opacity: 0.7 }}>{what}</div>
            {bars.map(([label, v, shown]) => (
              <div key={label} style={{ margin: "6px 0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                  <span>{label}</span><b style={{ fontVariantNumeric: "tabular-nums" }}>{shown}</b>
                </div>
                <div style={{ height: 12, marginTop: 4, background: "rgba(128,128,128,0.15)", borderRadius: 4 }}>
                  {v > 0 && <div style={{ width: `${(100 * v) / max}%`, minWidth: 1, height: 12, background: teal, borderRadius: 4 }} />}
                </div>
              </div>
            ))}
          </div>
        );
      })}
      <figcaption style={{ fontSize: 13, opacity: 0.7, marginTop: 6 }}>
        Bars to scale, at least a pixel wide. A re-run that costs nothing has no bar.
      </figcaption>
    </figure>
  );
};
