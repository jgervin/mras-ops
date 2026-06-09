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
