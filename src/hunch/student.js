// A hunch distilled student in a web page. `hunch export SPEC` writes this file beside student.json.
//
//   import { load } from "./student.js";
//   const student = await load("./student.json");
//   await student.check();                                   // this page answers as the exporting machine did
//   const { intent } = await student.answer({ text: "Where is my card?" });
//   // intent: { label, p, margin, route }: the student's own answer, in judge()'s shape. It never asks the
//   // teacher: route "review" means below act, and asking your server is the page's job
//
// It repeats distill.answer() step for step: the row becomes the text the encoder read in training, the frozen
// encoder embeds it in this tab, and each question's linear layer and temperature turn that into probabilities.
// Nothing is sent anywhere.
//
// The student runs on the visitor's machine, so they can change what it answers, and student.json is public.
// For a decision that protects something, judge the row on your server as well, or only there.
//
// The page fetches the ONNX runtime and tokenizer library from jsdelivr and the encoder's files from Hugging Face,
// each at a pinned version; the encoder is kept in the browser's Cache storage, so a visitor downloads it once.
// To load them from somewhere else pass `files` (a base URL holding the encoder's files), `Tokenizer`, and `ort`:
// onnxruntime-web's bundle for the backend in use (ort.wasm for WebAssembly, ort.webgpu for WebGPU).
//
// A WebAssembly answer runs on the thread that asked for it. In a page that is the main thread, which waits:
// unnoticeable for MiniLM, up to 0.7 s for EmbeddingGemma 2 without WebGPU. The module runs unchanged in a module
// Web Worker.

const RUNTIME = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.31.1/dist/";
const TOKENIZERS = "https://cdn.jsdelivr.net/npm/@huggingface/tokenizers@0.2.0/dist/tokenizers.min.mjs";
const TOLERANCE = 0.005; // check(): how far a probability may be from the exporting machine's
const COSINE = 0.999; // check(): how close an embedding must be (the same file on another runtime gives 0.99999)

const round4 = (v) => Math.round(v * 1e4) / 1e4;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// An encoder file's bytes, kept in this browser under its pinned URL. Hugging Face answers each download with a
// redirect to a signed, short-lived address, so the browser's own HTTP cache fetched the encoder again on every
// page load. Kept in parts: Chrome's Cache storage refused EmbeddingGemma 2's 314 MB file as one entry.
const PART = 64 * 2 ** 20;

async function once(cache, url) {
  const key = (i) => `${url}${url.includes("?") ? "&" : "?"}part=${i}`;
  const index = await cache?.match(key("index")).then((r) => r?.json()).catch(() => null);
  if (index) {
    const parts = await Promise.all(Array.from({ length: index.parts }, (_, i) => cache.match(key(i))));
    if (parts.every(Boolean)) {
      const out = new Uint8Array(index.size);
      let at = 0;
      for (const part of parts) {
        const b = new Uint8Array(await part.arrayBuffer());
        out.set(b, at);
        at += b.length;
      }
      if (at === index.size) return out;
    }
  }
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const out = new Uint8Array(await r.arrayBuffer());
  try {  // the index goes in last, so a half-written file is never taken for a whole one
    const n = Math.ceil(out.length / PART) || 1;
    for (let i = 0; i < n; i++) await cache.put(key(i), new Response(out.slice(i * PART, (i + 1) * PART)));
    await cache.put(key("index"), new Response(JSON.stringify({ size: out.length, parts: n })));
  } catch {
    // no Cache storage (plain http), no room, or not allowed: it is fetched again next time
  }
  return out;
}

const floats = (b64) => new Float32Array(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer);

// spec.state_of and distill.text_of: line endings normalised, `clip` counted in code points (as Python counts),
// then the bare column for `state: text`, or "column: value | column: value" for a list.
function textOf(student, row) {
  const columns = typeof student.state === "string" ? [student.state] : student.state;
  const sent = columns.map((c) => {
    if (typeof row[c] !== "string") throw new TypeError(`row.${c} must be a string, got ${row[c] === null ? "null" : typeof row[c]}`);
    const v = row[c].replace(/\r\n?/g, "\n");
    if (!(c in student.clip)) return v;
    const n = student.clip[c], points = Array.from(v);
    if (points.length <= Math.abs(n)) return v;
    return n > 0 ? points.slice(0, n).join("") + "…" : "…" + points.slice(n).join("");
  });
  return typeof student.state === "string" ? sent[0] : columns.map((c, i) => `${c}: ${sent[i]}`).join(" | ");
}

// answers.decide and answers.route, on probabilities already rounded to 4 places in the order of q.classes.
function decide(q, p) {
  const of = (c) => (q.classes.includes(c) ? p[q.classes.indexOf(c)] : 0);
  let label, conf, margin;
  if (q.type === "noul") {
    const yes = of("yes");
    [label, conf, margin] = [yes >= 0.5 ? "yes" : "no", Math.max(yes, 1 - yes), Math.abs(yes - 0.5) * 2];
  } else if (q.type === "choice") {
    const sorted = [...p].sort((a, b) => b - a);
    [label, conf, margin] = [q.classes[p.indexOf(sorted[0])], sorted[0], sorted[0] - (sorted.length > 1 ? sorted[1] : 0)];
  } else {  // score: the label is the nearest level to the expected score, the confidence is the likeliest level's
    const levels = q.legend.map((_, i) => of(String(i)));
    const score = round4(levels.reduce((t, v, i) => t + i * v, 0));
    let level = Math.round(score);
    if (Math.abs(score - Math.trunc(score)) === 0.5 && level % 2) level -= 1;  // Python rounds a half to the even level
    [label, conf, margin] = [`${level}:${q.legend[level]}`, Math.max(...levels), 1 - Math.abs(score - level) * 2];
  }
  const need = q.act == null ? null : typeof q.act === "object" ? q.act[label] : q.act;
  return { label, p: conf, margin: round4(margin), route: need == null ? "" : conf >= need ? "act" : "review" };
}

export async function load(url, { ort, Tokenizer, files, provider } = {}) {
  const student = await getJSON(url);
  if (student.format !== 1) throw new Error(`${url}: format ${student.format}; this student.js reads format 1 (export both files together)`);
  const enc = student.encoder;
  // WebGPU where the encoder is faster on it and the browser has it; WebAssembly runs every encoder everywhere
  provider ??= enc.webgpu && globalThis.navigator?.gpu ? "webgpu" : "wasm";
  if (provider === "webgpu" && !globalThis.navigator?.gpu) throw new Error("this browser has no WebGPU; leave `provider` out to run on WebAssembly");
  ort ??= await import(RUNTIME + (provider === "webgpu" ? "ort.webgpu.min.mjs" : "ort.wasm.min.mjs"));
  Tokenizer ??= (await import(TOKENIZERS)).Tokenizer;
  const hub = `https://huggingface.co/${enc.repo}/resolve/`, base = files ?? hub + enc.revision + "/";
  const cache = await globalThis.caches?.open("hunch-student").catch(() => null);
  for (const old of (await cache?.keys()) ?? []) if (old.url.startsWith(hub) && !old.url.startsWith(base)) cache.delete(old);  // an earlier revision
  const bytes = (name) => once(cache, base + name);
  const json = async (name) => JSON.parse(new TextDecoder().decode(await bytes(name)));
  const [tokenizerJSON, tokenizerConfig, model, data] = await Promise.all([
    json("tokenizer.json"), json("tokenizer_config.json"), bytes(enc.model), enc.data ? bytes(enc.data) : null]);
  const session = await ort.InferenceSession.create(model, {
    executionProviders: [provider],
    externalData: data ? [{ path: enc.data.split("/").pop(), data }] : undefined,
  });
  const tokenizer = new Tokenizer(tokenizerJSON, tokenizerConfig);
  const layers = Object.fromEntries(Object.entries(student.questions).map(([qid, q]) => [qid, { W: floats(q.W), b: floats(q.b) }]));

  let last = Promise.resolve();  // the runtime takes one run at a time: answers asked together wait their turn
  function embed(text) {
    const next = last.then(() => run(text));
    last = next.catch(() => {});
    return next;
  }

  async function run(text) {
    let ids = Array.from(tokenizer.encode(text).ids, Number);
    if (ids.length > enc.max_tokens) ids = [...ids.slice(0, enc.max_tokens - 1), ids[ids.length - 1]];  // keep the closing token
    const n = ids.length, ints = (xs) => new ort.Tensor("int64", BigInt64Array.from(xs, BigInt), [1, n]);
    const feed = { input_ids: ints(ids), attention_mask: ints(ids.map(() => 1)) };
    if (enc.token_types) feed.token_type_ids = ints(ids.map(() => 0));
    for (const [name, shape] of Object.entries(enc.empty ?? {})) feed[name] = new ort.Tensor("float32", new Float32Array(0), shape);
    const out = (await session.run(feed))[enc.output];
    const d = out.dims[out.dims.length - 1], v = new Float64Array(d);
    const end = enc.pool === "cls" ? d : out.data.length;  // the first token's state, else the mean over tokens (or the one vector)
    for (let i = 0; i < end; i++) v[i % d] += out.data[i];
    const norm = Math.hypot(...v);
    return v.map((x) => x / norm);
  }

  // {question: probabilities}, in the order of its classes, rounded to 4 places as distill.answer rounds them
  function probabilities(x) {
    return Object.fromEntries(Object.entries(student.questions).map(([qid, q]) => {
      const { W, b } = layers[qid], k = q.classes.length, z = new Float64Array(k);
      for (let c = 0; c < k; c++) {
        let s = b[c];
        for (let j = 0; j < x.length; j++) s += x[j] * W[j * k + c];
        z[c] = s / q.temperature;
      }
      const top = Math.max(...z), e = Array.from(z, (v) => Math.exp(v - top)), sum = e.reduce((a, v) => a + v, 0);
      return [qid, e.map((v) => round4(v / sum))];
    }));
  }

  return {
    judgment: student.judgment,
    provider,
    // {question: {label, p, margin, route}} for one row: an object with the spec's state columns, as strings
    async answer(row) {
      const p = probabilities(await embed(textOf(student, row)));
      return Object.fromEntries(Object.entries(student.questions).map(([qid, q]) => [qid, decide(q, p[qid])]));
    },
    // Runs the made-up rows the export carries and compares with what the exporting machine got for them.
    // Resolves to {cosine, gap}: the lowest embedding similarity and the largest gap in probability. Throws when
    // this page is not running the student as it was measured.
    async check() {
      let cosine = 1, gap = 0;
      for (const probe of student.probes) {
        const x = await embed(textOf(student, probe.row)), got = probabilities(x);
        cosine = Math.min(cosine, floats(probe.embedding).reduce((t, v, i) => t + v * x[i], 0));
        for (const [qid, want] of Object.entries(probe.probabilities)) want.forEach((w, i) => { gap = Math.max(gap, Math.abs(w - got[qid][i])); });
      }
      if (!(cosine >= COSINE && gap <= TOLERANCE)) {
        throw new Error(`this page does not run the student as exported: embeddings match to ${cosine.toFixed(4)} (needs ${COSINE}), ` +
                        `probabilities to within ${gap.toFixed(4)} (needs ${TOLERANCE}). The encoder, tokenizer or runtime it loaded differs.`);
      }
      return { cosine, gap };
    },
  };
}
