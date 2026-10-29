import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GlossaryOutputView from "./GlossaryOutputView";
import type { Settings } from "@/types";

const api = vi.hoisted(() => ({ getSettings: vi.fn(), updateSettings: vi.fn() }));
vi.mock("@/lib/api", () => api);

const props = {
  notes: "ARR: annual recurring revenue\nKafka: event streaming\narr: duplicate suggestion",
  isGenerating: false, failed: false, onRegenerate: vi.fn(), onExtractMore: vi.fn(),
};

beforeEach(() => {
  vi.resetAllMocks();
  api.getSettings.mockResolvedValue({ glossary_terms: "arr: our existing definition\nARRival: keep this" });
  api.updateSettings.mockImplementation(async (settings: Settings) => settings);
});

describe("glossary suggestions checklist", () => {
  it("matches only the exact personal term, ignoring case and duplicate suggestions", async () => {
    api.getSettings.mockResolvedValue({ glossary_terms: "  aRr : our definition\nKafkaesque: unrelated", install_glossary_terms: "Kafka: shared" });
    render(<GlossaryOutputView {...props} />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "ARR: annual recurring revenue" })).toBeChecked());
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "Kafka: event streaming" })).not.toBeChecked();
  });

  it("reads fresh settings for each add and removal, preserving other personal terms", async () => {
    api.getSettings
      .mockResolvedValueOnce({ glossary_terms: "arr: our existing definition" })
      .mockResolvedValueOnce({ glossary_terms: "arr: our existing definition\nARRival: keep this\nNew: added elsewhere" })
      .mockResolvedValueOnce({ glossary_terms: "ARRival: keep this\nNew: added elsewhere\nKafka: event streaming" });
    render(<GlossaryOutputView {...props} />);
    const arr = screen.getByRole("checkbox", { name: "ARR: annual recurring revenue" });
    const kafka = screen.getByRole("checkbox", { name: "Kafka: event streaming" });
    await waitFor(() => expect(arr).toBeChecked());
    fireEvent.click(kafka);
    await waitFor(() => expect(kafka).toBeChecked());
    expect(api.updateSettings).toHaveBeenLastCalledWith({
      glossary_terms: "arr: our existing definition\nARRival: keep this\nNew: added elsewhere\nKafka: event streaming",
    });
    fireEvent.click(kafka);
    await waitFor(() => expect(kafka).not.toBeChecked());
    expect(api.updateSettings).toHaveBeenLastCalledWith({
      glossary_terms: "ARRival: keep this\nNew: added elsewhere",
    });
  });

  it.each([
    'Kubernetes, heard as "cubanetties"',
    '- kUbErNeTeS = our cluster platform',
    '* Kubernetes: cluster tool, heard as "cubanetties"',
    '+ Kubernetes, heard as "cubanetties": common correction',
  ])("matches the user's own line %j and keeps it on untick", async (entry) => {
    api.getSettings.mockResolvedValue({ glossary_terms: `${entry}\nKubernetes-ish: preserve` });
    render(<GlossaryOutputView {...props} notes={'KUBERNETES: heard as "cubanetties"'} />);
    const checkbox = screen.getByRole("checkbox", { name: 'KUBERNETES: heard as "cubanetties"' });
    await waitFor(() => expect(checkbox).toBeChecked());
    fireEvent.click(checkbox);
    expect(await screen.findByText(/has its own line for this term/)).toBeInTheDocument();
    expect(checkbox).toBeChecked();
    expect(api.updateSettings).not.toHaveBeenCalled();
  });

  it("says when a first run or an Extract more run found nothing new", async () => {
    const { rerender } = render(<GlossaryOutputView {...props} notes="" />);
    expect(screen.getByText("No new terms found.")).toBeInTheDocument();
    rerender(<GlossaryOutputView {...props} />);
    expect(screen.queryByText("No new terms found.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Extract more" }));
    rerender(<GlossaryOutputView {...props} isGenerating />);
    expect(screen.queryByText("No new terms found.")).toBeNull();
    rerender(<GlossaryOutputView {...props} failed />);
    expect(screen.queryByText("No new terms found.")).toBeNull();
    rerender(<GlossaryOutputView {...props} />);
    expect(screen.getByText("No new terms found.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Extract more" }));
    rerender(<GlossaryOutputView {...props} isGenerating />);
    rerender(<GlossaryOutputView {...props} notes={`${props.notes}\nSLA: service-level agreement`} />);
    expect(screen.queryByText("No new terms found.")).toBeNull();
  });

  it("keeps the saved checkbox state when a settings write fails", async () => {
    api.updateSettings.mockRejectedValue({ response: { data: { detail: "Glossary is too long." } } });
    render(<GlossaryOutputView {...props} />);
    const kafka = screen.getByRole("checkbox", { name: "Kafka: event streaming" });
    await waitFor(() => expect(kafka).toBeEnabled());
    fireEvent.click(kafka);
    expect(await screen.findByRole("alert")).toHaveTextContent("Glossary is too long.");
    expect(kafka).not.toBeChecked();
    expect(kafka).toBeEnabled();
  });

  it("locks mutations while saving so two toggles cannot overwrite one another", async () => {
    let finish!: (settings: Settings) => void;
    api.updateSettings.mockImplementation(() => new Promise<Settings>((resolve) => { finish = resolve; }));
    render(<GlossaryOutputView {...props} />);
    const kafka = screen.getByRole("checkbox", { name: "Kafka: event streaming" });
    await waitFor(() => expect(kafka).toBeEnabled());
    fireEvent.click(kafka);
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledOnce());
    expect(kafka).toBeDisabled();
    expect(screen.getByRole("button", { name: "Extract more" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "ARR: annual recurring revenue" }));
    expect(api.updateSettings).toHaveBeenCalledOnce();
    await act(async () => finish({ glossary_terms: "Kafka: event streaming" }));
    expect(kafka).toBeChecked();
  });
});
