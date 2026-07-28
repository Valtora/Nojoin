import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const getModelsStatus = vi.fn();

vi.mock("@/lib/api", () => ({
  getModelsStatus: (...args: unknown[]) => getModelsStatus(...args),
  deleteModel: vi.fn(),
}));

import AiTranscriptionSection from "./AiTranscriptionSection";
import type { AISettingsModels } from "./useAISettingsModels";
import type { Settings } from "@/types";

const modelStatus = (downloaded: boolean) => ({
  whisper: { downloaded: true, path: null, checked_paths: [] },
  parakeet: { downloaded, path: null, checked_paths: [] },
  canary: { downloaded: false, path: null, checked_paths: [] },
  pyannote: { downloaded: true, path: null, checked_paths: [] },
  embedding: { downloaded: true, path: null, checked_paths: [] },
  segmentation: { downloaded: true, path: null, checked_paths: [] },
});

const renderSection = () => {
  const onPersist = vi.fn().mockResolvedValue(undefined);
  const startPreparation = vi.fn().mockResolvedValue(true);
  const models = {
    refreshStatus: vi.fn(),
    startPreparation,
  } as unknown as AISettingsModels;

  render(
    <AiTranscriptionSection
      settings={{ transcription_backend: "whisper" } as Settings}
      onPersist={onPersist}
      isAdmin
      models={models}
    />,
  );

  return { onPersist, startPreparation };
};

const selectParakeet = () =>
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "parakeet" },
  });

describe("AiTranscriptionSection download prompt", () => {
  beforeEach(() => {
    getModelsStatus.mockReset();
  });

  it("offers to download a model that is not on the server yet", async () => {
    getModelsStatus.mockResolvedValue(modelStatus(false));
    const { onPersist, startPreparation } = renderSection();

    selectParakeet();

    expect(await screen.findByText("Download Parakeet now?")).toBeTruthy();
    // The change is saved either way; only the download is in question.
    expect(onPersist).toHaveBeenCalledWith(
      expect.objectContaining({ transcription_backend: "parakeet" }),
    );

    fireEvent.click(screen.getByText("Download now"));
    await waitFor(() => expect(startPreparation).toHaveBeenCalledWith("active"));
  });

  it("does not prompt for a model that is already downloaded", async () => {
    getModelsStatus.mockResolvedValue(modelStatus(true));
    const { onPersist, startPreparation } = renderSection();

    selectParakeet();

    await waitFor(() => expect(onPersist).toHaveBeenCalled());
    await waitFor(() => expect(getModelsStatus).toHaveBeenCalled());
    expect(screen.queryByText("Download Parakeet now?")).toBeNull();
    expect(startPreparation).not.toHaveBeenCalled();
  });

  it("keeps the model change when the download is declined", async () => {
    getModelsStatus.mockResolvedValue(modelStatus(false));
    const { onPersist, startPreparation } = renderSection();

    selectParakeet();
    fireEvent.click(await screen.findByText("Download later"));

    await waitFor(() =>
      expect(screen.queryByText("Download Parakeet now?")).toBeNull(),
    );
    expect(startPreparation).not.toHaveBeenCalled();
    expect(onPersist).toHaveBeenCalledWith(
      expect.objectContaining({ transcription_backend: "parakeet" }),
    );
  });
});
