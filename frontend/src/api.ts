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
  createAd(ad: AdCreate): Promise<AdRecord>;
  preview(component_id: string, props: Record<string, unknown>, base_video: string): Promise<PreviewResult>;
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
};
