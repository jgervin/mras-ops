import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { Authoring } from "./Authoring";
import type { Api } from "./api";

// A real draft-07 schema as emitted by the sidecar (M5 Task 1) for a component's props.
const PROPS_SCHEMA = {
  $schema: "http://json-schema.org/draft-07/schema#",
  type: "object",
  additionalProperties: false,
  properties: {
    count: { type: "number", default: 6 },
    colors: { type: "array", items: { type: "string" }, default: ["#f39c12", "#e74c3c"] },
    speed: { type: "number", default: 1 },
    text: { type: "string" },
    color: { type: "string", default: "#ffffff" },
  },
  required: ["text"],
};

function makeFakeApi(overrides: Partial<Api> = {}): Api {
  return {
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "neon", status: "ready", propsSchema: {} }),
    listComponents: vi.fn().mockResolvedValue([]),
    listAds: vi.fn().mockResolvedValue([]),
    listBaseVideos: vi.fn().mockResolvedValue(["/assets/standard.mp4", "/assets/standard2.mp4"]),
    createAd: vi.fn().mockResolvedValue({ id: "a1" }),
    preview: vi.fn().mockResolvedValue({ url: "http://example.com/preview.mp4" }),
    ...overrides,
  };
}

async function uploadAComponent() {
  const file = new File(["()=>{}"], "neon.js", { type: "application/javascript" });
  fireEvent.change(screen.getByLabelText(/component file/i), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "Neon" } });
  fireEvent.click(screen.getByRole("button", { name: /upload/i }));
  await waitFor(() => screen.getByRole("button", { name: /preview/i }));
}

test("base video is a dropdown populated from the pool (no free-text path to mistype)", async () => {
  render(<Authoring api={makeFakeApi()} />);
  // The Create Ad base-video selector is a <select> with the pool options.
  const sel = (await screen.findByLabelText("ad base video")) as HTMLSelectElement;
  expect(sel.tagName).toBe("SELECT");
  const opts = Array.from(sel.querySelectorAll("option")).map(o => o.value);
  expect(opts).toContain("/assets/standard.mp4");
  expect(opts).toContain("/assets/standard2.mp4");
});

test("preview: after upload, selecting a base video and clicking Preview renders the video", async () => {
  const fakeApi = makeFakeApi();
  render(<Authoring api={fakeApi} />);
  await uploadAComponent();

  fireEvent.change(screen.getByLabelText("base video"), { target: { value: "/assets/standard2.mp4" } });
  fireEvent.click(screen.getByRole("button", { name: /preview/i }));

  await waitFor(() => {
    expect(fakeApi.preview).toHaveBeenCalledWith("c1", {}, "/assets/standard2.mp4");
    expect(document.querySelector("video")).toHaveAttribute("src", "http://example.com/preview.mp4");
  });
});

test("preview: Props (JSON) textarea is editable and parsed on preview", async () => {
  const fakeApi = makeFakeApi();
  render(<Authoring api={fakeApi} />);
  await uploadAComponent();

  const ta = screen.getByLabelText(/props \(json\)/i) as HTMLTextAreaElement;
  // Intermediate (invalid) JSON must stick — the old code re-parsed every keystroke and reverted it.
  fireEvent.change(ta, { target: { value: '{"count":' } });
  expect(ta.value).toBe('{"count":');

  fireEvent.change(ta, { target: { value: '{"count":50}' } });
  fireEvent.change(screen.getByLabelText("base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.click(screen.getByRole("button", { name: /preview/i }));
  await waitFor(() => {
    expect(fakeApi.preview).toHaveBeenCalledWith("c1", { count: 50 }, "/assets/standard.mp4");
  });
});

test("creating an ad auto-renders a preview of the finished ad and shows it", async () => {
  const fakeApi = makeFakeApi({
    createAd: vi.fn().mockResolvedValue({
      id: "a1", name: "nike", component_id: "c1", base_video: "/assets/standard.mp4",
      default_props: { count: 120 }, personalized_field: "text", is_active: true,
    }),
    preview: vi.fn().mockResolvedValue({ url: "http://example.com/ad.mp4" }),
  });
  render(<Authoring api={fakeApi} />);

  fireEvent.change(await screen.findByLabelText("ad base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.click(screen.getByRole("button", { name: /create ad/i }));

  await waitFor(() => expect(fakeApi.preview).toHaveBeenCalled());
  const [compId, props, base] = (fakeApi.preview as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(compId).toBe("c1");
  expect(base).toBe("/assets/standard.mp4");
  expect(props.count).toBe(120);
  expect(props.text).toBeTruthy(); // personalized field filled with a sample name

  await waitFor(() => {
    const shown = Array.from(document.querySelectorAll("video")).some(
      v => v.getAttribute("src") === "http://example.com/ad.mp4",
    );
    expect(shown).toBe(true);
  });
});

test("preview: schema-driven fields render one labeled input per prop, pre-filled with defaults, and flow typed into preview", async () => {
  const fakeApi = makeFakeApi({
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "neon", status: "ready", propsSchema: PROPS_SCHEMA }),
  });
  render(<Authoring api={fakeApi} />);
  await uploadAComponent();

  const previewSection = (screen.getByRole("heading", { name: /^preview$/i }).closest("section")) as HTMLElement;

  // No raw JSON textarea — a labeled field per prop instead, each pre-filled with its default.
  expect(within(previewSection).queryByLabelText(/props \(json\)/i)).toBeNull();
  expect((within(previewSection).getByLabelText("count") as HTMLInputElement).value).toBe("6");
  expect((within(previewSection).getByLabelText("speed") as HTMLInputElement).value).toBe("1");
  expect((within(previewSection).getByLabelText("color") as HTMLInputElement).value).toBe("#ffffff");
  // array-of-primitive default shows as a comma-separated list.
  expect((within(previewSection).getByLabelText("colors") as HTMLInputElement).value).toBe("#f39c12, #e74c3c");
  // required prop with no default renders empty.
  expect((within(previewSection).getByLabelText("text") as HTMLInputElement).value).toBe("");

  // Editing a field and previewing flows values to the API, typed (numbers as numbers, arrays as arrays).
  fireEvent.change(within(previewSection).getByLabelText("text"), { target: { value: "Promo" } });
  fireEvent.change(screen.getByLabelText("base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

  await waitFor(() => expect(fakeApi.preview).toHaveBeenCalled());
  const [compId, props, base] = (fakeApi.preview as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(compId).toBe("c1");
  expect(base).toBe("/assets/standard.mp4");
  expect(props).toMatchObject({ count: 6, speed: 1, color: "#ffffff", colors: ["#f39c12", "#e74c3c"], text: "Promo" });
});

test("create ad: selecting a component renders its schema fields pre-filled and flows typed default_props into createAd", async () => {
  const fakeApi = makeFakeApi({
    listComponents: vi.fn().mockResolvedValue([{ id: "c1", slug: "neon", status: "ready", props_schema: PROPS_SCHEMA }]),
  });
  render(<Authoring api={fakeApi} />);

  const createSection = (await screen.findByRole("heading", { name: /create ad/i })).closest("section") as HTMLElement;

  // Selecting the component replaces the Default props (JSON) textarea with schema-driven fields.
  fireEvent.change(within(createSection).getByLabelText("component"), { target: { value: "c1" } });

  expect(within(createSection).queryByLabelText(/default props \(json\)/i)).toBeNull();
  expect((within(createSection).getByLabelText("count") as HTMLInputElement).value).toBe("6");
  expect((within(createSection).getByLabelText("colors") as HTMLInputElement).value).toBe("#f39c12, #e74c3c");
  expect((within(createSection).getByLabelText("text") as HTMLInputElement).value).toBe("");

  fireEvent.change(within(createSection).getByLabelText("text"), { target: { value: "Hi" } });
  fireEvent.change(within(createSection).getByLabelText("ad base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.click(within(createSection).getByRole("button", { name: /create ad/i }));

  await waitFor(() => expect(fakeApi.createAd).toHaveBeenCalled());
  const arg = (fakeApi.createAd as ReturnType<typeof vi.fn>).mock.calls[0][0];
  expect(arg.default_props).toMatchObject({ count: 6, speed: 1, color: "#ffffff", colors: ["#f39c12", "#e74c3c"], text: "Hi" });
});

test("help panel is hidden by default", () => {
  render(<Authoring api={makeFakeApi()} />);
  expect(screen.queryByText(/Worked example/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Authoring guide/i)).not.toBeInTheDocument();
});

test("clicking ? button reveals help panel", () => {
  render(<Authoring api={makeFakeApi()} />);
  fireEvent.click(screen.getByRole("button", { name: /help/i }));
  expect(screen.getByText(/Worked example/i)).toBeInTheDocument();
  expect(screen.getByText(/Authoring guide/i)).toBeInTheDocument();
});

test("clicking ? button again hides help panel", () => {
  render(<Authoring api={makeFakeApi()} />);
  const helpBtn = screen.getByRole("button", { name: /help/i });
  fireEvent.click(helpBtn);
  expect(screen.getByText(/Worked example/i)).toBeInTheDocument();
  fireEvent.click(helpBtn);
  expect(screen.queryByText(/Worked example/i)).not.toBeInTheDocument();
});

test("uploads component and shows status", async () => {
  const fakeApi = makeFakeApi();
  render(<Authoring api={fakeApi} />);

  const file = new File(["()=>{}"], "neon.js", { type: "application/javascript" });
  fireEvent.change(screen.getByLabelText(/component file/i), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "Neon" } });
  fireEvent.click(screen.getByRole("button", { name: /upload/i }));

  await waitFor(() => {
    expect(fakeApi.uploadComponent).toHaveBeenCalledWith("Neon", file);
    expect(screen.getByText(/ready/i)).toBeInTheDocument();
  });
});
