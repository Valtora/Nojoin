import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import AiModelDependenciesSection from "./AiModelDependenciesSection";
import type { ModelSource, SystemModelStatus } from "@/types";

const ready = (source?: ModelSource) => ({
  downloaded: true,
  path: "/cache/model",
  checked_paths: [],
  source,
});

const statusWithEmbedding = (source?: ModelSource): SystemModelStatus => ({
  whisper: ready(),
  parakeet: ready(),
  canary: ready(),
  pyannote: ready("cache"),
  embedding: ready(source),
  segmentation: ready("cache"),
});

const renderSection = (modelStatus: SystemModelStatus) => {
  const handleDeleteModel = vi.fn();
  render(
    <AiModelDependenciesSection
      modelStatus={modelStatus}
      deleting={null}
      handleDeleteModel={handleDeleteModel}
      isAdmin
      downloadProgress={null}
      preparationRunning={false}
      startPreparation={vi.fn().mockResolvedValue(true)}
    />,
  );
  return handleDeleteModel;
};

const deleteEmbedding = () =>
  screen.getByRole("button", {
    name: "Delete Voice Embedding",
  }) as HTMLButtonElement;

describe("AiModelDependenciesSection delete", () => {
  it("offers Delete for a model in Nojoin's own cache", () => {
    const handleDeleteModel = renderSection(statusWithEmbedding("cache"));

    fireEvent.click(deleteEmbedding());

    expect(deleteEmbedding().disabled).toBe(false);
    expect(handleDeleteModel).toHaveBeenCalledWith("embedding");
  });

  it("disables Delete and says why for a model outside Nojoin's cache", () => {
    const handleDeleteModel = renderSection(statusWithEmbedding("external"));

    fireEvent.click(deleteEmbedding());

    expect(deleteEmbedding().disabled).toBe(true);
    expect(deleteEmbedding().title).toMatch(/outside Nojoin's model cache/);
    expect(screen.getByText("External")).toBeTruthy();
    expect(handleDeleteModel).not.toHaveBeenCalled();
  });

  it("keeps Delete disabled for a bundled model", () => {
    renderSection(statusWithEmbedding("bundled"));

    expect(deleteEmbedding().disabled).toBe(true);
    expect(deleteEmbedding().title).toBe("Bundled repo asset");
    expect(screen.queryByText("External")).toBeNull();
  });
});
