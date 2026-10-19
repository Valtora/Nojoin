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

const renderSection = (
  modelStatus: SystemModelStatus,
  preparationRunning = false,
) => {
  const handleDeleteModel = vi.fn();
  render(
    <AiModelDependenciesSection
      modelStatus={modelStatus}
      deleting={null}
      handleDeleteModel={handleDeleteModel}
      isAdmin
      downloadProgress={null}
      preparationRunning={preparationRunning}
      startPreparation={vi.fn().mockResolvedValue(true)}
    />,
  );
  return handleDeleteModel;
};

const deleteEmbedding = () =>
  screen.getByRole("button", {
    name: "Delete Voice embedding",
  }) as HTMLButtonElement;

const queryDeleteEmbedding = () =>
  screen.queryByRole("button", { name: "Delete Voice embedding" });

describe("AiModelDependenciesSection delete", () => {
  it("offers Delete for a model in Nojoin's own cache", () => {
    const handleDeleteModel = renderSection(statusWithEmbedding("cache"));

    fireEvent.click(deleteEmbedding());

    expect(deleteEmbedding().disabled).toBe(false);
    expect(handleDeleteModel).toHaveBeenCalledWith("embedding");
  });

  it("offers no Delete and says why for a model outside Nojoin's cache", () => {
    renderSection(statusWithEmbedding("external"));

    expect(queryDeleteEmbedding()).toBeNull();
    expect(
      screen.getByText("Kept in a cache outside Nojoin's, so it is used but not deleted from here."),
    ).toBeTruthy();
    expect(screen.getByText("External")).toBeTruthy();
  });

  it("offers no Delete and says why for a bundled model", () => {
    renderSection(statusWithEmbedding("bundled"));

    expect(queryDeleteEmbedding()).toBeNull();
    expect(screen.getByText("Bundled with Nojoin, so it cannot be deleted here.")).toBeTruthy();
    expect(screen.queryByText("External")).toBeNull();
  });
});

const missing = (partial?: boolean) => ({
  downloaded: false,
  path: null,
  checked_paths: [],
  partial,
});

const statusWithParakeet = (partial?: boolean): SystemModelStatus => ({
  ...statusWithEmbedding("cache"),
  parakeet: missing(partial),
});

const CLEAR_PARAKEET = {
  name: "Clear partial download of Parakeet",
};

const clearParakeet = () =>
  screen.getByRole("button", CLEAR_PARAKEET) as HTMLButtonElement;

describe("AiModelDependenciesSection partial download", () => {
  it("offers to clear a partial download of a missing model", () => {
    const handleDeleteModel = renderSection(statusWithParakeet(true));

    fireEvent.click(clearParakeet());

    expect(clearParakeet().disabled).toBe(false);
    expect(handleDeleteModel).toHaveBeenCalledWith("parakeet");
  });

  it("offers nothing to delete for a model with no partial download", () => {
    renderSection(statusWithParakeet());

    expect(screen.queryByRole("button", CLEAR_PARAKEET)).toBeNull();
  });

  it("keeps the clear button disabled while a preparation is running", () => {
    const handleDeleteModel = renderSection(statusWithParakeet(true), true);

    fireEvent.click(clearParakeet());

    expect(clearParakeet().disabled).toBe(true);
    expect(handleDeleteModel).not.toHaveBeenCalled();
  });
});
