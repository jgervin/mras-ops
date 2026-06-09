import { useEffect, useRef, useState } from "react";
import type { Api, AdCreate, ComponentRecord, AdRecord } from "./api";

// Stand-in viewer name used when previewing an ad's personalized field.
const SAMPLE_NAME = "Jordan";

// --- JSON-schema-driven prop fields ---------------------------------------------------------
// A component declares its inputs as a JSON schema (draft-07) emitted by the sidecar from the
// component's zod schema. We render one real, labelled field per primitive prop pre-filled with
// its default, so advertisers never guess prop names in a blank JSON box.

type JsonSchemaProperty = {
  type?: string;
  default?: unknown;
  enum?: unknown[];
  items?: { type?: string };
};
type SchemaProperties = Record<string, JsonSchemaProperty>;

const PRIMITIVE_TYPES = new Set(["string", "number", "integer", "boolean", "array"]);

// Pull a schema's renderable `properties` (or null when none parse). Accepts either the camelCase
// `propsSchema` from POST /components or the snake_case `props_schema` from GET /components.
function schemaPropertiesOf(propsSchema: unknown): SchemaProperties | null {
  const props = (propsSchema as { properties?: SchemaProperties } | undefined)?.properties;
  return props && Object.keys(props).length > 0 ? props : null;
}

function requiredOf(propsSchema: unknown): string[] {
  return (propsSchema as { required?: string[] } | undefined)?.required ?? [];
}

// Pre-fill each field with its default, as the raw string the input edits.
function schemaDefaults(properties: SchemaProperties): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, p] of Object.entries(properties)) {
    if (p.default === undefined) out[key] = "";
    else if (Array.isArray(p.default)) out[key] = p.default.join(", ");
    else if (typeof p.default === "object") out[key] = JSON.stringify(p.default);
    else out[key] = String(p.default);
  }
  return out;
}

// Coerce the raw field strings back to typed props for the preview/createAd payloads. Empty
// optional fields are omitted so the component falls back to its own zod default.
function coerceProps(properties: SchemaProperties, raw: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, p] of Object.entries(properties)) {
    const v = raw[key] ?? "";
    if (p.type === "boolean") { out[key] = v === "true"; continue; }
    if (v === "") continue;
    if (p.type === "number" || p.type === "integer") {
      const n = Number(v);
      if (!Number.isNaN(n)) out[key] = n;
      continue;
    }
    if (p.type === "array") {
      const items = v.split(",").map(s => s.trim()).filter(s => s !== "");
      const numeric = p.items?.type === "number" || p.items?.type === "integer";
      out[key] = numeric ? items.map(Number) : items;
      continue;
    }
    if (p.type === "string") { out[key] = v; continue; }
    // Unsupported (nested object / union): the field holds a raw JSON value — parse it, drop if invalid.
    try { out[key] = JSON.parse(v); } catch { /* leave it out */ }
  }
  return out;
}

function fieldTypeLabel(p: JsonSchemaProperty): string {
  if (p.enum) return "enum";
  if (p.type === "array") return `${p.items?.type ?? "string"}[]`;
  if (p.type && PRIMITIVE_TYPES.has(p.type)) return p.type;
  return "json";
}

const fieldStyle = { marginLeft: 8, width: 280 };

// One labelled input per prop. The label carries the prop name, an optional required marker, and
// the type, so the advertiser sees exactly what each field expects.
function SchemaPropFields({
  properties, required, values, onChange, idPrefix,
}: {
  properties: SchemaProperties;
  required: string[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  idPrefix: string;
}) {
  return (
    <>
      {Object.entries(properties).map(([key, p]) => {
        const id = `${idPrefix}-${key}`;
        const raw = values[key] ?? "";
        let field;
        if (p.enum) {
          field = (
            <select id={id} value={raw} onChange={e => onChange(key, e.target.value)} style={fieldStyle}>
              {(p.enum as unknown[]).map(opt => (
                <option key={String(opt)} value={String(opt)}>{String(opt)}</option>
              ))}
            </select>
          );
        } else if (p.type === "boolean") {
          field = (
            <input id={id} type="checkbox" checked={raw === "true"}
              onChange={e => onChange(key, String(e.target.checked))} />
          );
        } else if (p.type === "number" || p.type === "integer") {
          field = (
            <input id={id} type="number" value={raw} onChange={e => onChange(key, e.target.value)} style={fieldStyle} />
          );
        } else if (p.type === "array") {
          field = (
            <input id={id} value={raw} placeholder="comma, separated" onChange={e => onChange(key, e.target.value)} style={fieldStyle} />
          );
        } else if (p.type === "string") {
          field = (
            <input id={id} type="text" value={raw} onChange={e => onChange(key, e.target.value)} style={fieldStyle} />
          );
        } else {
          field = (
            <input id={id} value={raw} placeholder="JSON value" onChange={e => onChange(key, e.target.value)} style={fieldStyle} />
          );
        }
        return (
          <div key={key} style={{ marginBottom: 6 }}>
            <label htmlFor={id} style={{ display: "inline-block", minWidth: 150 }}>
              {key}{required.includes(key) ? " *" : ""} <span style={{ color: "#888" }}>({fieldTypeLabel(p)})</span>
            </label>
            {field}
          </div>
        );
      })}
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
  const [propValues, setPropValues] = useState<Record<string, string>>({});
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
  // Schema-driven default-prop field values for the selected component (raw strings; coerced on submit).
  const [adPropValues, setAdPropValues] = useState<Record<string, string>>({});
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

  async function handleUpload() {
    if (!file || !name) return;
    setUploading(true);
    setUploadResult(null);
    try {
      const result = await api.uploadComponent(name, file);
      setUploadResult(result);
      // Pre-fill the schema-driven fields with each prop's default (empty when no schema).
      const props = schemaPropertiesOf(result.propsSchema);
      setPropValues(props ? schemaDefaults(props) : {});
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setUploadResult({ status: "error", error: String(e) });
    } finally {
      setUploading(false);
    }
  }

  async function handlePreview() {
    if (!uploadResult?.id) return;
    const previewSchema = schemaPropertiesOf(uploadResult.propsSchema);
    let props: Record<string, unknown>;
    if (previewSchema) {
      props = coerceProps(previewSchema, propValues);
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
    let parsedProps: Record<string, unknown> = {};
    if (adSchemaProps) {
      parsedProps = coerceProps(adSchemaProps, adPropValues);
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

  // derive schema-driven prop fields for the just-uploaded component (Preview section)
  const schemaProps = schemaPropertiesOf(uploadResult?.propsSchema);
  const schemaRequired = requiredOf(uploadResult?.propsSchema);

  // derive schema-driven prop fields for the component selected in Create Ad
  const selectedComponent = components.find(c => c.id === adForm.component_id);
  const adRawSchema = selectedComponent?.propsSchema ?? selectedComponent?.props_schema;
  const adSchemaProps = schemaPropertiesOf(adRawSchema);
  const adRequired = requiredOf(adRawSchema);

  // When the selected component changes, pre-fill its default-prop fields (or clear when no schema).
  useEffect(() => {
    setAdPropValues(adSchemaProps ? schemaDefaults(adSchemaProps) : {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adForm.component_id, components]);

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
          {schemaProps ? (
            <SchemaPropFields
              properties={schemaProps}
              required={schemaRequired}
              values={propValues}
              onChange={(key, value) => setPropValues(p => ({ ...p, [key]: value }))}
              idPrefix="preview-prop"
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
            aria-label="ad component"
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
          {adSchemaProps ? (
            <>
              <label style={{ display: "block", marginBottom: 4 }}>Default props</label>
              <SchemaPropFields
                properties={adSchemaProps}
                required={adRequired}
                values={adPropValues}
                onChange={(key, value) => setAdPropValues(p => ({ ...p, [key]: value }))}
                idPrefix="ad-prop"
              />
            </>
          ) : (
            <>
              <label>Default props (JSON)</label>
              <textarea
                aria-label="default props (json)"
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
