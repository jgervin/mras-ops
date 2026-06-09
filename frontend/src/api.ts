/// <reference types="vite/client" />

const OPS_API = import.meta.env.VITE_OPS_API_URL ?? "http://localhost:8080";
const COMPOSER = import.meta.env.VITE_COMPOSER_URL ?? "http://localhost:8002";

export interface ComponentRecord {
  id: string;
  slug: string;
  status: "ready" | "failed" | string;
  propsSchema: Record<string, unknown>;
  error?: string;
}

export interface AdRecord {
  id: string;
  name: string;
  base_video: string;
  component_id: string | null;
  default_props: Record<string, unknown>;
  personalized_field: string | null;
  is_active: boolean;
}

export interface AdCreate {
  name: string;
  base_video: string;
  component_id?: string | null;
  default_props?: Record<string, unknown>;
  personalized_field?: string | null;
  is_active?: boolean;
}

export interface PreviewResult {
  url?: string;
  error?: string;
}

export interface Api {
  uploadComponent(name: string, file: File): Promise<ComponentRecord>;
  listComponents(): Promise<ComponentRecord[]>;
  listAds(): Promise<AdRecord[]>;
  listBaseVideos(): Promise<string[]>;
  createAd(ad: AdCreate): Promise<AdRecord>;
  preview(component_id: string, props: Record<string, unknown>, base_video: string): Promise<PreviewResult>;
  deleteAd(id: string): Promise<void>;
  deleteComponent(id: string): Promise<void>;
}

export const api: Api = {
  async uploadComponent(name, file) {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("file", file);
    const res = await fetch(`${OPS_API}/components`, { method: "POST", body: fd });
    return res.json();
  },

  async listComponents() {
    const res = await fetch(`${OPS_API}/components`);
    return res.json();
  },

  async listAds() {
    const res = await fetch(`${OPS_API}/ads`);
    return res.json();
  },

  // Base videos are the composer's idle pool (/playlist returns full URLs); the composer reads
  // them by container path, so map each URL to its /assets/<name> path.
  async listBaseVideos() {
    const res = await fetch(`${COMPOSER}/playlist`);
    const data = await res.json();
    return (data.videos ?? []).map((u: string) => {
      try { return new URL(u).pathname; } catch { return u; }
    });
  },

  async createAd(ad) {
    const res = await fetch(`${OPS_API}/ads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ad),
    });
    return res.json();
  },

  async preview(component_id, props, base_video) {
    const res = await fetch(`${COMPOSER}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ component_id, props, base_video }),
    });
    return res.json();
  },

  async deleteAd(id) {
    const res = await fetch(`${OPS_API}/ads/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`delete ad failed (${res.status})`);
  },

  async deleteComponent(id) {
    const res = await fetch(`${OPS_API}/components/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({} as { detail?: string }));
      throw new Error(body.detail ?? `delete component failed (${res.status})`);
    }
  },
};
