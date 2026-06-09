import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { Authoring } from "./Authoring";
import type { Api } from "./api";

function makeFakeApi(overrides: Partial<Api> = {}): Api {
  return {
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "neon", status: "ready", propsSchema: {} }),
    listComponents: vi.fn().mockResolvedValue([]),
    listAds: vi.fn().mockResolvedValue([]),
    listBaseVideos: vi.fn().mockResolvedValue(["/assets/standard.mp4", "/assets/standard2.mp4"]),
    createAd: vi.fn().mockResolvedValue({ id: "a1" }),
    preview: vi.fn().mockResolvedValue({ url: "http://example.com/preview.mp4" }),
    deleteAd: vi.fn().mockResolvedValue(undefined),
    deleteComponent: vi.fn().mockResolvedValue(undefined),
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

  // Scope to the Preview section: Create Ad now also has a (fallback) "default props (json)" box.
  const ta = within(previewSection()).getByLabelText(/props \(json\)/i) as HTMLTextAreaElement;
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

test("an ad whose base video is not in the pool is flagged and its preview is disabled", async () => {
  const fakeApi = makeFakeApi({
    listBaseVideos: vi.fn().mockResolvedValue(["/assets/standard.mp4", "/assets/standard2.mp4"]),
    listAds: vi.fn().mockResolvedValue([
      { id: "bad", name: "snow2", base_video: "/assets/standard1.mp4", component_id: "c1", default_props: {}, personalized_field: "text", is_active: true },
      { id: "good", name: "ok-ad", base_video: "/assets/standard.mp4", component_id: "c1", default_props: {}, personalized_field: "text", is_active: true },
    ]),
  });
  render(<Authoring api={fakeApi} />);

  const brokenItem = (await screen.findByText(/snow2/)).closest("li") as HTMLElement;
  expect(within(brokenItem).getByText(/base video missing/i)).toBeInTheDocument();
  expect(within(brokenItem).getByRole("button", { name: /preview/i })).toBeDisabled();

  const okItem = screen.getByText(/ok-ad/).closest("li") as HTMLElement;
  expect(within(okItem).getByRole("button", { name: /preview/i })).toBeEnabled();
});

test("deleting an ad calls the API and removes it from the list", async () => {
  const fakeApi = makeFakeApi({
    listAds: vi.fn().mockResolvedValue([
      { id: "a1", name: "doomed-ad", base_video: "/assets/standard.mp4", component_id: "c1", default_props: {}, personalized_field: "text", is_active: true },
    ]),
  });
  render(<Authoring api={fakeApi} />);

  const item = (await screen.findByText(/doomed-ad/)).closest("li") as HTMLElement;
  fireEvent.click(within(item).getByRole("button", { name: /delete/i }));

  await waitFor(() => {
    expect(fakeApi.deleteAd).toHaveBeenCalledWith("a1");
    expect(screen.queryByText(/doomed-ad/)).not.toBeInTheDocument();
  });
});

test("deleting a component calls the API and removes it from the list", async () => {
  const fakeApi = makeFakeApi({
    listComponents: vi.fn().mockResolvedValue([
      { id: "c1", slug: "snowy", status: "ready", propsSchema: {} },
    ]),
  });
  render(<Authoring api={fakeApi} />);

  // Scope to the Components list — the slug also appears as a Create-Ad <select> option.
  const section = (await screen.findByText(/Components \(/)).closest("section") as HTMLElement;
  const item = within(section).getByText(/snowy/).closest("li") as HTMLElement;
  fireEvent.click(within(item).getByRole("button", { name: /delete/i }));

  await waitFor(() => {
    expect(fakeApi.deleteComponent).toHaveBeenCalledWith("c1");
    expect(within(section).queryByText(/snowy/)).not.toBeInTheDocument();
  });
});

test("a failed component delete surfaces the error in the Components section (not buried under Ads)", async () => {
  const fakeApi = makeFakeApi({
    listComponents: vi.fn().mockResolvedValue([
      { id: "c1", slug: "snowy", status: "ready", propsSchema: {} },
    ]),
    deleteComponent: vi
      .fn()
      .mockRejectedValue(new Error("component is used by existing ads — delete those ads first")),
  });
  render(<Authoring api={fakeApi} />);

  const section = (await screen.findByText(/Components \(/)).closest("section") as HTMLElement;
  const item = within(section).getByText(/snowy/).closest("li") as HTMLElement;
  fireEvent.click(within(item).getByRole("button", { name: /delete/i }));

  // The error must appear where the user clicked — inside the Components section — not under Ads.
  await waitFor(() => {
    expect(within(section).getByText(/used by existing ads/i)).toBeInTheDocument();
  });
  // Delete was refused, so the component remains.
  expect(within(section).getByText(/snowy/)).toBeInTheDocument();
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

// JSON-schema (draft-07) emitted by the sidecar for a component with a zod schema.
const SCHEMA = {
  $schema: "http://json-schema.org/draft-07/schema#",
  type: "object",
  additionalProperties: false,
  properties: {
    count: { type: "number", default: 6 },
    colors: { type: "array", items: { type: "string" }, default: ["#f39c12", "#e74c3c"] },
    speed: { type: "number", default: 1 },
    text: { type: "string" },
    color: { type: "string", default: "#ffffff" },
    mode: { type: "string", enum: ["calm", "wild"], default: "calm" },
  },
  required: ["text"],
};

function previewSection() {
  return screen.getByRole("heading", { name: /^preview$/i }).closest("section") as HTMLElement;
}
function createAdSection() {
  return screen.getByRole("heading", { name: /create ad/i }).closest("section") as HTMLElement;
}

test("preview: schema-driven fields render labeled (name + type), pre-filled with each prop's default", async () => {
  const fakeApi = makeFakeApi({
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "fish", status: "ready", propsSchema: SCHEMA }),
  });
  render(<Authoring api={fakeApi} />);
  await uploadAComponent();
  const sec = previewSection();

  // Each primitive gets a real, type-labelled field pre-filled with its default.
  expect(within(sec).getByLabelText(/count \(number\)/i)).toHaveValue(6);
  expect(within(sec).getByLabelText(/speed \(number\)/i)).toHaveValue(1);
  expect(within(sec).getByLabelText(/^color \(string\)/i)).toHaveValue("#ffffff");
  // array-of-primitive → comma-separated input pre-filled from the default array
  expect(within(sec).getByLabelText(/colors \(string\[\]\)/i)).toHaveValue("#f39c12, #e74c3c");
  // enum (string + enum) → a <select> defaulting to the schema default
  const mode = within(sec).getByLabelText(/mode \(enum\)/i) as HTMLSelectElement;
  expect(mode.tagName).toBe("SELECT");
  expect(mode).toHaveValue("calm");
  // required prop with no default → rendered empty (validation UI is out of scope)
  expect(within(sec).getByLabelText(/text/i)).toHaveValue("");
  // The raw "Props (JSON)" textarea must NOT be shown when a schema is present.
  expect(within(sec).queryByLabelText(/props \(json\)/i)).not.toBeInTheDocument();
});

test("preview: edited + defaulted fields flow as correctly-typed props into the preview call", async () => {
  const fakeApi = makeFakeApi({
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "fish", status: "ready", propsSchema: SCHEMA }),
  });
  render(<Authoring api={fakeApi} />);
  await uploadAComponent();
  const sec = previewSection();

  fireEvent.change(within(sec).getByLabelText(/count \(number\)/i), { target: { value: "12" } });
  fireEvent.change(within(sec).getByLabelText(/text/i), { target: { value: "Hello" } });
  fireEvent.change(screen.getByLabelText("base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.click(within(sec).getByRole("button", { name: /^preview/i }));

  await waitFor(() => {
    expect(fakeApi.preview).toHaveBeenCalledWith(
      "c1",
      {
        count: 12,            // number, coerced from the input
        speed: 1,             // untouched default preserved
        color: "#ffffff",
        colors: ["#f39c12", "#e74c3c"], // array of primitives
        mode: "calm",         // enum
        text: "Hello",        // edited string
      },
      "/assets/standard.mp4",
    );
  });
});

test("create ad: selecting a schema component renders default-filled fields; values flow to createAd", async () => {
  const fakeApi = makeFakeApi({
    // NOTE: GET /components returns snake_case `props_schema` (the upload response is camelCase).
    listComponents: vi.fn().mockResolvedValue([
      { id: "c1", slug: "fish", status: "ready", props_schema: SCHEMA },
    ]),
    createAd: vi.fn().mockResolvedValue({
      id: "a1", name: "n", component_id: "c1", base_video: "/assets/standard.mp4",
      default_props: {}, personalized_field: null, is_active: true,
    }),
  });
  render(<Authoring api={fakeApi} />);

  fireEvent.change(await screen.findByLabelText("ad base video"), { target: { value: "/assets/standard.mp4" } });
  fireEvent.change(await screen.findByLabelText("ad component"), { target: { value: "c1" } });

  const sec = createAdSection();
  expect(within(sec).getByLabelText(/count \(number\)/i)).toHaveValue(6);
  fireEvent.change(within(sec).getByLabelText(/count \(number\)/i), { target: { value: "99" } });

  fireEvent.click(within(sec).getByRole("button", { name: /create ad/i }));

  await waitFor(() => expect(fakeApi.createAd).toHaveBeenCalled());
  const sent = (fakeApi.createAd as ReturnType<typeof vi.fn>).mock.calls[0][0];
  expect(sent.default_props.count).toBe(99);
  expect(sent.default_props.colors).toEqual(["#f39c12", "#e74c3c"]);
  expect(sent.default_props.mode).toBe("calm");
});

test("create ad: a component without a parseable schema falls back to the JSON textarea", async () => {
  const fakeApi = makeFakeApi({
    listComponents: vi.fn().mockResolvedValue([
      { id: "c2", slug: "plain", status: "ready", props_schema: {} },
    ]),
  });
  render(<Authoring api={fakeApi} />);

  fireEvent.change(await screen.findByLabelText("ad component"), { target: { value: "c2" } });
  const sec = createAdSection();
  expect(within(sec).getByLabelText(/default props \(json\)/i)).toBeInTheDocument();
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
