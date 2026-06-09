import { useEffect, useRef, useState } from "react";
import type { Api, AdCreate, ComponentRecord, AdRecord } from "./api";

// Stand-in viewer name used when previewing an ad's personalized field.
const SAMPLE_NAME = "Jordan";

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

  // --- delete (keep the registry from stacking up) ---
  // Per-section delete errors so each surfaces next to the list it belongs to.
  const [adDeleteError, setAdDeleteError] = useState<string | null>(null);
  const [componentDeleteError, setComponentDeleteError] = useState<string | null>(null);

  async function handleDeleteAd(id: string) {
    setAdDeleteError(null);
    try {
      await api.deleteAd(id);
      setAds(prev => prev.filter(a => a.id !== id));
    } catch (e) {
      setAdDeleteError(String(e));
    }
  }

  async function handleDeleteComponent(id: string) {
    setComponentDeleteError(null);
    try {
      await api.deleteComponent(id);
      setComponents(prev => prev.filter(c => c.id !== id));
    } catch (e) {
      // e.g. 409 when the component is still used by ads
      setComponentDeleteError(String(e));
    }
  }

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
      // reset prop values for new component
      setPropValues({});
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      setUploadResult({ status: "error", error: String(e) });
    } finally {
      setUploading(false);
    }
  }

  async function handlePreview() {
    if (!uploadResult?.id) return;
    const hasSchema = !!(uploadResult.propsSchema as { properties?: unknown } | undefined)?.properties;
    let props: Record<string, unknown>;
    if (hasSchema) {
      props = propValues;
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
    try {
      parsedProps = JSON.parse(adPropsJson);
    } catch {
      setAdError("default_props is not valid JSON");
      return;
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

  // derive schema-driven prop fields
  const schemaProps = uploadResult?.propsSchema
    ? (uploadResult.propsSchema as { properties?: Record<string, unknown> }).properties ?? null
    : null;

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
            Object.keys(schemaProps).map(key => (
              <div key={key} style={{ marginBottom: 4 }}>
                <label>{key}</label>
                <input
                  value={propValues[key] ?? ""}
                  onChange={e => setPropValues(p => ({ ...p, [key]: e.target.value }))}
                  style={{ marginLeft: 8 }}
                />
              </div>
            ))
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
          <label>Default props (JSON)</label>
          <textarea
            rows={3}
            value={adPropsJson}
            onChange={e => setAdPropsJson(e.target.value)}
            style={{ marginLeft: 8, width: 300 }}
          />
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
              {c.slug} — {c.status}{" "}
              <button onClick={() => handleDeleteComponent(c.id)} title="delete component" style={{ marginLeft: 8, cursor: "pointer" }}>🗑 delete</button>
            </li>
          ))}
        </ul>
        {componentDeleteError && <div style={{ color: "#f88", marginTop: 8 }}>{componentDeleteError}</div>}
      </section>

      <section>
        <h3>Ads ({ads.length})</h3>
        <ul>
          {ads.map(a => {
            // An ad is broken if its base video isn't in the pool (e.g. a stale record from
            // before the base-video dropdown, or a clip later removed from /assets). Don't offer
            // a preview that can only fail. Gate on a loaded pool to avoid false flags during load.
            const baseOk = baseVideos.length === 0 || baseVideos.includes(a.base_video);
            return (
              <li key={a.id} style={baseOk ? undefined : { color: "#f88" }}>
                {a.name} | base: {a.base_video} | active: {String(a.is_active)}
                {!baseOk && " | ⚠ base video missing"}{" "}
                <button
                  onClick={() => renderAdPreview(a)}
                  disabled={!baseOk}
                  title={baseOk ? undefined : "base video not found — fix or remove this ad"}
                  style={{ marginLeft: 8, cursor: baseOk ? "pointer" : "not-allowed" }}
                >▶ preview</button>{" "}
                <button onClick={() => handleDeleteAd(a.id)} title="delete ad" style={{ marginLeft: 4, cursor: "pointer" }}>🗑 delete</button>
              </li>
            );
          })}
        </ul>
        {adDeleteError && <div style={{ color: "#f88", marginTop: 8 }}>{adDeleteError}</div>}
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
