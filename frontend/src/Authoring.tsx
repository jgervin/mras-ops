import { useEffect, useRef, useState } from "react";
import type { Api, AdCreate, ComponentRecord, AdRecord } from "./api";

// Stand-in viewer name used when previewing an ad's personalized field.
const SAMPLE_NAME = "Jordan";

// ── schema-driven prop fields ────────────────────────────────────────────────
// Turn a component's JSON-Schema (draft-07) `properties` into a flat list of typed
// fields. Returns null when the schema can't be rendered as simple fields (no
// properties, or any prop is an unsupported type) — the caller falls back to the
// editable JSON textarea. v1 scope: string / number / boolean / enum / array-of-primitive.
type PropFieldKind = "string" | "number" | "boolean" | "enum" | "array";
interface PropField {
  name: string;
  kind: PropFieldKind;
  itemKind?: "string" | "number"; // array element type
  options?: string[];             // enum choices
  default?: unknown;
  required: boolean;
}

function fieldsFromSchema(schema: unknown): PropField[] | null {
  const s = schema as { properties?: Record<string, unknown>; required?: string[] } | undefined;
  const props = s?.properties;
  if (!props || typeof props !== "object" || Object.keys(props).length === 0) return null;
  const required = new Set(s?.required ?? []);
  const fields: PropField[] = [];
  for (const [name, raw] of Object.entries(props)) {
    const d = raw as { type?: string; enum?: unknown[]; items?: { type?: string }; default?: unknown };
    const req = required.has(name);
    let field: PropField | null = null;
    if (Array.isArray(d.enum) && (d.type === "string" || d.type === undefined)) {
      field = { name, kind: "enum", options: d.enum.map(String), default: d.default, required: req };
    } else if (d.type === "string") {
      field = { name, kind: "string", default: d.default, required: req };
    } else if (d.type === "number" || d.type === "integer") {
      field = { name, kind: "number", default: d.default, required: req };
    } else if (d.type === "boolean") {
      field = { name, kind: "boolean", default: d.default, required: req };
    } else if (d.type === "array" && (d.items?.type === "string" || d.items?.type === "number" || d.items?.type === "integer")) {
      field = { name, kind: "array", itemKind: d.items.type === "integer" ? "number" : (d.items.type as "string" | "number"), default: d.default, required: req };
    }
    if (!field) return null; // unsupported (nested object / union / …) → JSON textarea fallback
    fields.push(field);
  }
  return fields;
}

// Initial editable display values (strings for text inputs, boolean for checkboxes),
// pre-filled from each prop's schema default.
function defaultFieldValues(fields: PropField[]): Record<string, unknown> {
  const v: Record<string, unknown> = {};
  for (const f of fields) {
    if (f.kind === "boolean") v[f.name] = f.default === true;
    else if (f.kind === "array") v[f.name] = Array.isArray(f.default) ? (f.default as unknown[]).join(", ") : "";
    else v[f.name] = f.default === undefined || f.default === null ? "" : String(f.default);
  }
  return v;
}

// Coerce the editable display values back into typed props for the API.
function buildProps(fields: PropField[], values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (f.kind === "boolean") { out[f.name] = raw === true; continue; }
    if (f.kind === "array") {
      const parts = String(raw ?? "").split(",").map(p => p.trim()).filter(p => p.length > 0);
      out[f.name] = f.itemKind === "number" ? parts.map(Number) : parts;
      continue;
    }
    const str = String(raw ?? "").trim();
    if (str === "") continue; // optional / empty → omit
    if (f.kind === "number") {
      const n = Number(str);
      if (!Number.isNaN(n)) out[f.name] = n;
      continue;
    }
    out[f.name] = str; // string | enum
  }
  return out;
}

function fieldTypeLabel(f: PropField): string {
  return f.kind === "array" ? `${f.itemKind}[]` : f.kind;
}

// One labeled input per prop. `aria-label` carries the bare prop name as a clean a11y
// handle; the visible label also shows the type (and a * for required props).
function PropFields({ fields, values, onChange }: {
  fields: PropField[];
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
}) {
  return (
    <>
      {fields.map(f => (
        <div key={f.name} style={{ marginBottom: 4 }}>
          <label>
            {f.name} <span style={{ opacity: 0.6 }}>({fieldTypeLabel(f)}{f.required ? "*" : ""})</span>
          </label>
          {f.kind === "boolean" ? (
            <input
              aria-label={f.name}
              type="checkbox"
              checked={values[f.name] === true}
              onChange={e => onChange(f.name, e.target.checked)}
              style={{ marginLeft: 8 }}
            />
          ) : f.kind === "enum" ? (
            <select
              aria-label={f.name}
              value={String(values[f.name] ?? "")}
              onChange={e => onChange(f.name, e.target.value)}
              style={{ marginLeft: 8 }}
            >
              {f.options!.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input
              aria-label={f.name}
              type={f.kind === "number" ? "number" : "text"}
              value={String(values[f.name] ?? "")}
              placeholder={f.kind === "array" ? `comma-separated ${f.itemKind}s` : undefined}
              onChange={e => onChange(f.name, e.target.value)}
              style={{ marginLeft: 8, width: 300 }}
            />
          )}
        </div>
      ))}
    </>
  );
}

export function Authoring({ api }: { api: Api }) {
  // --- help panel state ---
  const [helpOpen, setHelpOpen] = useState(false);

  // --- upload state ---
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadResult, setUploadResult] = useState<{ status: string; id?: string; error?: string; propsSchema?: Record<string, unknown> } | null>(null);
  const [uploading, setUploading] = useState(false);

  // --- preview state ---
  // Schema-driven field values (string for text/number/enum/array display, boolean for checkboxes).
  const [propValues, setPropValues] = useState<Record<string, unknown>>({});
  // Raw JSON text for the no-schema fallback. Kept as a string (parsed only on Preview) so the
  // field stays editable — parsing on every keystroke reverted intermediate/invalid JSON.
  const [previewPropsJson, setPreviewPropsJson] = useState("{}");
  const [baseVideo, setBaseVideo] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // --- component/ad lists ---
  const [components, setComponents] = useState<ComponentRecord[]>([]);
  const [ads, setAds] = useState<AdRecord[]>([]);
  const [baseVideos, setBaseVideos] = useState<string[]>([]);

  // --- create-ad form ---
  const [adForm, setAdForm] = useState<AdCreate>({
    name: "",
    base_video: "",
    component_id: null,
    default_props: {},
    personalized_field: null,
    is_active: true,
  });
  const [adPropsJson, setAdPropsJson] = useState("{}");
  // Schema-driven default-prop values for the selected component (mirrors propValues, for Create Ad).
  const [adPropValues, setAdPropValues] = useState<Record<string, unknown>>({});
  const [adError, setAdError] = useState<string | null>(null);
  const [adCreated, setAdCreated] = useState(false);

  // --- finished-ad preview modal ---
  const [adPreviewUrl, setAdPreviewUrl] = useState<string | null>(null);
  const [adPreviewError, setAdPreviewError] = useState<string | null>(null);
  const [adPreviewRendering, setAdPreviewRendering] = useState(false);

  // Render the finished ad (base + component + its default props, with the personalized field
  // filled by a sample name) and pop it up so the advertiser can watch what they just made.
  async function renderAdPreview(ad: AdRecord) {
    setAdPreviewError(null);
    setAdPreviewUrl(null);
    setAdPreviewRendering(true);
    const props: Record<string, unknown> = { ...(ad.default_props ?? {}) };
    if (ad.personalized_field) props[ad.personalized_field] = SAMPLE_NAME;
    try {
      const res = await api.preview(ad.component_id ?? "", props, ad.base_video);
      if (res.url) setAdPreviewUrl(res.url);
      else setAdPreviewError(res.error ?? "unknown error");
    } catch (e) {
      setAdPreviewError(String(e));
    } finally {
      setAdPreviewRendering(false);
    }
  }

  const closeAdPreview = () => {
    setAdPreviewUrl(null);
    setAdPreviewError(null);
    setAdPreviewRendering(false);
  };

  useEffect(() => {
    api.listComponents().then(setComponents).catch(() => {});
    api.listAds().then(setAds).catch(() => {});
    // Load the base-video pool and default both selectors to the first one (never empty).
    api.listBaseVideos().then(vs => {
      setBaseVideos(vs);
      if (vs.length) {
        setBaseVideo(prev => prev || vs[0]);
        setAdForm(f => (f.base_video ? f : { ...f, base_video: vs[0] }));
      }
    }).catch(() => {});
  }, [api]);

  // When the Create-Ad component selection changes, pre-fill its schema fields with defaults.
  useEffect(() => {
    const comp = components.find(c => c.id === adForm.component_id);
    const fields = fieldsFromSchema(comp?.props_schema);
    setAdPropValues(fields ? defaultFieldValues(fields) : {});
  }, [adForm.component_id, components]);

  async function handleUpload() {
    if (!file || !name) return;
    setUploading(true);
    setUploadResult(null);
    try {
      const result = await api.uploadComponent(name, file);
      setUploadResult(result);
      // Pre-fill the schema-driven fields with the component's defaults (or clear for the JSON fallback).
      const fields = fieldsFromSchema(result.propsSchema);
      setPropValues(fields ? defaultFieldValues(fields) : {});
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setUploadResult({ status: "error", error: String(e) });
    } finally {
      setUploading(false);
    }
  }

  async function handlePreview() {
    if (!uploadResult?.id) return;
    const fields = fieldsFromSchema(uploadResult.propsSchema);
    let props: Record<string, unknown>;
    if (fields) {
      props = buildProps(fields, propValues);
    } else {
      try {
        props = JSON.parse(previewPropsJson);
      } catch {
        setPreviewError("Props is not valid JSON");
        return;
      }
    }
    setPreviewing(true);
    setPreviewUrl(null);
    setPreviewError(null);
    try {
      const result = await api.preview(uploadResult.id, props, baseVideo.trim());
      if (result.url) setPreviewUrl(result.url);
      else setPreviewError(result.error ?? "unknown error");
    } catch (e) {
      setPreviewError(String(e));
    } finally {
      setPreviewing(false);
    }
  }

  async function handleCreateAd() {
    setAdError(null);
    setAdCreated(false);
    const comp = components.find(c => c.id === adForm.component_id);
    const fields = fieldsFromSchema(comp?.props_schema);
    let parsedProps: Record<string, unknown> = {};
    if (fields) {
      parsedProps = buildProps(fields, adPropValues);
    } else {
      try {
        parsedProps = JSON.parse(adPropsJson);
      } catch {
        setAdError("default_props is not valid JSON");
        return;
      }
    }
    try {
      const created = await api.createAd({ ...adForm, base_video: adForm.base_video.trim(), default_props: parsedProps });
      setAds(prev => [created, ...prev]);
      setAdCreated(true);
      // Immediately render the finished ad and pop it up so the advertiser sees the result.
      renderAdPreview(created);
    } catch (e) {
      setAdError(String(e));
    }
  }

  // Derive schema-driven prop fields for Preview (uploaded component) and Create Ad (selected
  // component). Null when there are no parseable/renderable fields → JSON-textarea fallback.
  const previewFields = fieldsFromSchema(uploadResult?.propsSchema);
  const selectedComp = components.find(c => c.id === adForm.component_id);
  const adFields = fieldsFromSchema(selectedComp?.props_schema);

  return (
    <div style={{ fontFamily: "monospace", padding: 16, background: "#111", color: "#eee", minHeight: "100vh" }}>
      <h2 style={{ display: "inline-block", marginRight: 12 }}>Component Authoring</h2>
      <button
        aria-label="help"
        onClick={() => setHelpOpen(o => !o)}
        style={{ fontSize: 12, padding: "2px 8px", cursor: "pointer" }}
      >?</button>

      {helpOpen && (
        <section style={{ marginBottom: 24, border: "1px solid #444", padding: 12, background: "#1a1a1a" }}>
          <h3 style={{ marginTop: 0 }}>Authoring guide</h3>

          <h4>Components (styles)</h4>
          <ul>
            <li>A component is a Remotion .tsx that defines how text animates and declares its inputs via a zod <code>schema</code>.</li>
            <li>Upload it once; it appears under Components as <code>ready</code>, then select it in Create Ad.</li>
            <li>Example component: <code>/Users/jn/code/mras-overlays/examples/HelloName.tsx</code> (its props: text, color)</li>
          </ul>

          <h4>Create Ad fields</h4>
          <ul>
            <li><strong>Ad name</strong> — a label for you (e.g. hello-jason).</li>
            <li><strong>Base video</strong> — the clip the overlay is drawn on. Must be a path the composer can read: <code>/assets/standard.mp4</code> (also /assets/standard2.mp4, /assets/standard3.mp4, /assets/standard4.mp4).</li>
            <li><strong>Component</strong> — one of your uploaded components (the animation style).</li>
            <li><strong>Default props (JSON)</strong> — fixed inputs passed to the component for everyone; keys must match the component's schema. Example: <code>{"{"}"color":"#ff2d2d"{"}"}</code></li>
            <li><strong>Personalized field</strong> — the ONE prop replaced at runtime with the recognized viewer's name. Example: <code>text</code>. Leave blank for a non-personalized ad.</li>
            <li><strong>Active</strong> — marks this ad live. A recognized viewer's trigger renders the active custom ad (most recent wins). Unchecked = saved but not served.</li>
          </ul>

          <h4>Worked example (HelloName)</h4>
          <ol>
            <li>Upload <code>/Users/jn/code/mras-overlays/examples/HelloName.tsx</code> (Name: HelloName) → wait for "ready".</li>
            <li>Create Ad: Ad name <code>hello-jason</code>, Base video <code>/assets/standard.mp4</code>, Component <code>HelloName</code>, Default props <code>{"{"}"color":"#ff2d2d"{"}"}</code>, Personalized field <code>text</code>, Active ✓ → Create.</li>
            <li>Trigger an identified viewer → the kiosk plays /assets/standard.mp4 with their name animated in red.</li>
          </ol>

          <p><strong>Precedence:</strong> identified viewer → active custom ad (M4); else the built-in name overlay (M3); unidentified viewer → nothing plays (the kiosk idle pool keeps looping).</p>
        </section>
      )}

      {/* ── Upload ── */}
      <section style={{ marginBottom: 24 }}>
        <h3>Upload Component</h3>
        <div>
          <label htmlFor="comp-name">Name</label>
          <input
            id="comp-name"
            aria-label="name"
            value={name}
            onChange={e => setName(e.target.value)}
            style={{ marginLeft: 8 }}
          />
        </div>
        <div style={{ marginTop: 8 }}>
          <label htmlFor="comp-file">Component file</label>
          <input
            id="comp-file"
            aria-label="component file"
            type="file"
            ref={fileRef}
            onChange={e => setFile(e.target.files?.[0] ?? null)}
            style={{ marginLeft: 8 }}
          />
        </div>
        <button onClick={handleUpload} disabled={uploading || !file || !name} style={{ marginTop: 8 }}>
          {uploading ? "Uploading…" : "Upload"}
        </button>
        {uploadResult && (
          <div style={{ marginTop: 8, color: uploadResult.status === "ready" ? "#6f6" : "#f88" }}>
            Status: {uploadResult.status}
            {uploadResult.error && <span> — {uploadResult.error}</span>}
          </div>
        )}
      </section>

      {/* ── Props + Preview ── */}
      {uploadResult?.status === "ready" && (
        <section style={{ marginBottom: 24 }}>
          <h3>Preview</h3>
          {previewFields ? (
            <PropFields
              fields={previewFields}
              values={propValues}
              onChange={(n, v) => setPropValues(p => ({ ...p, [n]: v }))}
            />
          ) : (
            <div>
              <label>Props (JSON)</label>
              <textarea
                aria-label="Props (JSON)"
                rows={4}
                value={previewPropsJson}
                onChange={e => setPreviewPropsJson(e.target.value)}
                style={{ marginLeft: 8, width: 300 }}
              />
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <label>Base video</label>
            <select
              aria-label="base video"
              value={baseVideo}
              onChange={e => setBaseVideo(e.target.value)}
              style={{ marginLeft: 8, width: 300 }}
            >
              {baseVideos.length === 0 && <option value="">(no base videos in the pool)</option>}
              {baseVideos.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <button onClick={handlePreview} disabled={previewing} style={{ marginTop: 8 }}>
            {previewing ? "Previewing…" : "Preview"}
          </button>
          {previewUrl && (
            <div style={{ marginTop: 8 }}>
              <video controls src={previewUrl} style={{ maxWidth: 640 }} />
            </div>
          )}
          {previewError && <div style={{ color: "#f88", marginTop: 8 }}>Error: {previewError}</div>}
        </section>
      )}

      {/* ── Create Ad ── */}
      <section style={{ marginBottom: 24 }}>
        <h3>Create Ad</h3>
        <div>
          <label>Ad name</label>
          <input value={adForm.name} onChange={e => setAdForm(f => ({ ...f, name: e.target.value }))} style={{ marginLeft: 8 }} />
        </div>
        <div style={{ marginTop: 4 }}>
          <label>Base video</label>
          <select aria-label="ad base video" value={adForm.base_video} onChange={e => setAdForm(f => ({ ...f, base_video: e.target.value }))} style={{ marginLeft: 8, width: 300 }}>
            <option value="">(choose a base video)</option>
            {baseVideos.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
        <div style={{ marginTop: 4 }}>
          <label>Component</label>
          <select
            aria-label="component"
            value={adForm.component_id ?? ""}
            onChange={e => setAdForm(f => ({ ...f, component_id: e.target.value || null }))}
            style={{ marginLeft: 8 }}
          >
            <option value="">(none)</option>
            {components.map(c => (
              <option key={c.id} value={c.id}>{c.slug} [{c.status}]</option>
            ))}
          </select>
        </div>
        <div style={{ marginTop: 4 }}>
          {adFields ? (
            <PropFields
              fields={adFields}
              values={adPropValues}
              onChange={(n, v) => setAdPropValues(p => ({ ...p, [n]: v }))}
            />
          ) : (
            <>
              <label>Default props (JSON)</label>
              <textarea
                rows={3}
                value={adPropsJson}
                onChange={e => setAdPropsJson(e.target.value)}
                style={{ marginLeft: 8, width: 300 }}
              />
            </>
          )}
        </div>
        <div style={{ marginTop: 4 }}>
          <label>Personalized field</label>
          <input
            value={adForm.personalized_field ?? ""}
            onChange={e => setAdForm(f => ({ ...f, personalized_field: e.target.value || null }))}
            style={{ marginLeft: 8 }}
          />
        </div>
        <div style={{ marginTop: 4 }}>
          <label>
            <input
              type="checkbox"
              checked={adForm.is_active}
              onChange={e => setAdForm(f => ({ ...f, is_active: e.target.checked }))}
              style={{ marginRight: 4 }}
            />
            Active
          </label>
        </div>
        <button onClick={handleCreateAd} style={{ marginTop: 8 }}>Create Ad</button>
        {adError && <div style={{ color: "#f88", marginTop: 4 }}>{adError}</div>}
        {adCreated && <div style={{ color: "#6f6", marginTop: 4 }}>Ad created.</div>}
      </section>

      {/* ── Lists ── */}
      <section style={{ marginBottom: 24 }}>
        <h3>Components ({components.length})</h3>
        <ul>
          {components.map(c => (
            <li key={c.id} style={{ color: c.status === "ready" ? "#6f6" : "#f88" }}>
              {c.slug} — {c.status}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Ads ({ads.length})</h3>
        <ul>
          {ads.map(a => (
            <li key={a.id}>
              {a.name} | base: {a.base_video} | active: {String(a.is_active)}{" "}
              <button onClick={() => renderAdPreview(a)} style={{ marginLeft: 8, cursor: "pointer" }}>▶ preview</button>
            </li>
          ))}
        </ul>
      </section>

      {/* ── Finished-ad preview popup ── */}
      {(adPreviewRendering || adPreviewUrl || adPreviewError) && (
        <div
          onClick={closeAdPreview}
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
          }}
        >
          <div onClick={e => e.stopPropagation()} style={{ background: "#1a1a1a", border: "1px solid #444", padding: 20, borderRadius: 8, maxWidth: "90vw" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 16 }}>
              <strong>Your ad — preview (sample name: {SAMPLE_NAME})</strong>
              <button onClick={closeAdPreview} style={{ cursor: "pointer" }}>✕ close</button>
            </div>
            {adPreviewRendering && <div>Rendering your ad… (a few seconds)</div>}
            {adPreviewUrl && <video controls autoPlay src={adPreviewUrl} style={{ maxWidth: "80vw", maxHeight: "70vh", display: "block" }} />}
            {adPreviewError && <div style={{ color: "#f88" }}>Error: {adPreviewError}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
