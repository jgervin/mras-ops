import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { Authoring } from "./Authoring";
import type { Api } from "./api";

function makeFakeApi(overrides: Partial<Api> = {}): Api {
  return {
    uploadComponent: vi.fn().mockResolvedValue({ id: "c1", slug: "neon", status: "ready", propsSchema: {} }),
    listComponents: vi.fn().mockResolvedValue([]),
    listAds: vi.fn().mockResolvedValue([]),
    createAd: vi.fn().mockResolvedValue({ id: "a1" }),
    preview: vi.fn().mockResolvedValue({ url: "http://example.com/preview.mp4" }),
    ...overrides,
  };
}

test("preview: after upload, clicking Preview calls api.preview and renders video", async () => {
  const fakeApi = makeFakeApi();
  render(<Authoring api={fakeApi} />);

  // Upload first so the Preview section becomes visible
  const file = new File(["()=>{}"], "neon.js", { type: "application/javascript" });
  fireEvent.change(screen.getByLabelText(/component file/i), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "Neon" } });
  fireEvent.click(screen.getByRole("button", { name: /upload/i }));
  await waitFor(() => screen.getByRole("button", { name: /preview/i }));

  // Fill base video and click Preview
  fireEvent.change(screen.getByPlaceholderText(/path\/to\/base\.mp4/i), {
    target: { value: "videos/base.mp4" },
  });
  fireEvent.click(screen.getByRole("button", { name: /preview/i }));

  await waitFor(() => {
    expect(fakeApi.preview).toHaveBeenCalledWith("c1", {}, "videos/base.mp4");
    expect(document.querySelector("video")).toHaveAttribute("src", "http://example.com/preview.mp4");
  });
});

test("preview: Props (JSON) textarea is editable and parsed on preview", async () => {
  const fakeApi = makeFakeApi();
  render(<Authoring api={fakeApi} />);

  const file = new File(["()=>{}"], "neon.js", { type: "application/javascript" });
  fireEvent.change(screen.getByLabelText(/component file/i), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "Neon" } });
  fireEvent.click(screen.getByRole("button", { name: /upload/i }));
  await waitFor(() => screen.getByLabelText(/props \(json\)/i));

  const ta = screen.getByLabelText(/props \(json\)/i) as HTMLTextAreaElement;
  // Intermediate (invalid) JSON must stick — the old code re-parsed on every keystroke and
  // reverted the field, making it impossible to type.
  fireEvent.change(ta, { target: { value: '{"count":' } });
  expect(ta.value).toBe('{"count":');

  // Complete it; Preview parses the raw text and forwards the props.
  fireEvent.change(ta, { target: { value: '{"count":50}' } });
  fireEvent.change(screen.getByPlaceholderText(/path\/to\/base\.mp4/i), {
    target: { value: "videos/base.mp4" },
  });
  fireEvent.click(screen.getByRole("button", { name: /preview/i }));
  await waitFor(() => {
    expect(fakeApi.preview).toHaveBeenCalledWith("c1", { count: 50 }, "videos/base.mp4");
  });
});

test("help panel is hidden by default", () => {
  render(<Authoring api={makeFakeApi()} />);
  // "Worked example" only exists inside the help panel (not in the main form)
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
  const fileInput = screen.getByLabelText(/component file/i);
  const nameInput = screen.getByLabelText(/name/i);

  fireEvent.change(fileInput, { target: { files: [file] } });
  fireEvent.change(nameInput, { target: { value: "Neon" } });

  fireEvent.click(screen.getByRole("button", { name: /upload/i }));

  await waitFor(() => {
    expect(fakeApi.uploadComponent).toHaveBeenCalledWith("Neon", file);
    expect(screen.getByText(/ready/i)).toBeInTheDocument();
  });
});
