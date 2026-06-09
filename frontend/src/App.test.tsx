import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

// App imports the real api + EventSource; stub both so we can render it in jsdom.
vi.mock("./api", () => ({
  api: {
    listComponents: vi.fn().mockResolvedValue([]),
    listAds: vi.fn().mockResolvedValue([]),
    listBaseVideos: vi.fn().mockResolvedValue([]),
    uploadComponent: vi.fn(),
    createAd: vi.fn(),
    preview: vi.fn(),
  },
}));

class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null;
  close = vi.fn();
  constructor(public url: string) {}
}

beforeEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
});

describe("App tabs", () => {
  it("shows authoring by default and switches to the activity feed", async () => {
    const App = (await import("./App")).default;
    render(<App />);

    // Authoring tab is active by default (its file input is present); feed is hidden.
    expect(screen.getByLabelText(/component file/i)).toBeTruthy();
    expect(screen.queryByText(/MRAS Activity Feed/i)).toBeNull();

    // Switch to the feed tab.
    fireEvent.click(screen.getByRole("button", { name: /activity feed/i }));
    expect(screen.getByText(/MRAS Activity Feed/i)).toBeTruthy();
    expect(screen.queryByLabelText(/component file/i)).toBeNull();
  });
});
