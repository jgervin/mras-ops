import { useEffect, useRef, useState } from "react";
import type { Api, AdCreate, ComponentRecord, AdRecord } from "./api";

export function Authoring({ api }: { api: Api }) {
  // --- upload state ---
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadResult, setUploadResult] = useState<{ status: string; id?: string; error?: string; propsSchema?: Record<string, unknown> } | null>(null);
  const [uploading, setUploading] = useState(false);

  // --- preview state ---
  const [propValues, setPropValues] = useState<Record<string, string>>({});
  const [baseVideo, setBaseVideo] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // --- component/ad lists ---
  const [components, setComponents] = useState<ComponentRecord[]>([]);
  const [ads, setAds] = useState<AdRecord[]>([]);

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

  useEffect(() => {
    api.listComponents().then(setComponents).catch(() => {});
    api.listAds().then(setAds).catch(() => {});
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
    } finally {
      setUploading(false);
    }
  }

  async function handlePreview() {
    if (!uploadResult?.id) return;
    setPreviewing(true);
    setPreviewUrl(null);
    setPreviewError(null);
    const props = Object.fromEntries(Object.entries(propValues).map(([k, v]) => [k, v]));
    try {
      const result = await api.preview(uploadResult.id, props, baseVideo);
      if (result.url) setPreviewUrl(result.url);
      else setPreviewError(result.error ?? "unknown error");
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
      const created = await api.createAd({ ...adForm, default_props: parsedProps });
      setAds(prev => [created, ...prev]);
      setAdCreated(true);
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
      <h2>Component Authoring</h2>

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
                rows={4}
                value={JSON.stringify(propValues, null, 2)}
                onChange={e => {
                  try { setPropValues(JSON.parse(e.target.value)); } catch { /* ignore */ }
                }}
                style={{ marginLeft: 8, width: 300 }}
              />
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <label>Base video</label>
            <input
              value={baseVideo}
              onChange={e => setBaseVideo(e.target.value)}
              placeholder="path/to/base.mp4"
              style={{ marginLeft: 8, width: 300 }}
            />
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
          <input value={adForm.base_video} onChange={e => setAdForm(f => ({ ...f, base_video: e.target.value }))} style={{ marginLeft: 8, width: 300 }} />
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
              {a.name} | base: {a.base_video} | active: {String(a.is_active)}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
