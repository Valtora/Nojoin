import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { httpError } from "@/test/httpErrors";
import type { PeopleTag } from "@/types";

const getPeopleTags = vi.fn();
const createPeopleTag = vi.fn();
const updatePeopleTag = vi.fn();
const deletePeopleTag = vi.fn();

vi.mock("@/lib/api", () => ({
  getPeopleTags: (...args: unknown[]) => getPeopleTags(...args),
  createPeopleTag: (...args: unknown[]) => createPeopleTag(...args),
  updatePeopleTag: (...args: unknown[]) => updatePeopleTag(...args),
  deletePeopleTag: (...args: unknown[]) => deletePeopleTag(...args),
}));

import { PeopleTagSidebar } from "./PeopleTagSidebar";

const TAGS = [
  { id: 1, name: "Clients", color: "orange" },
  { id: 2, name: "Suppliers", color: "blue" },
] as PeopleTag[];

const messages = () =>
  useNotificationStore
    .getState()
    .activeNotifications.map(({ type, message }) => `${type}: ${message}`);

async function renderSidebar() {
  render(
    <PeopleTagSidebar
      selectedTagIds={[]}
      onToggleTag={vi.fn()}
      onClearFilters={vi.fn()}
    />,
  );
  await screen.findByText("Clients");
}

describe("PeopleTagSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    getPeopleTags.mockResolvedValue(TAGS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says why a rename was refused and keeps the name typed", async () => {
    updatePeopleTag.mockRejectedValue(
      httpError(400, "Tag with this name already exists"),
    );
    await renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Rename Clients" }));
    const field = screen.getByDisplayValue("Clients");
    fireEvent.change(field, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });

    expect(updatePeopleTag).toHaveBeenCalledWith(1, { name: "Suppliers" });
    expect(messages()).toEqual(["error: Tag with this name already exists"]);
    expect(screen.getByDisplayValue("Suppliers")).toBe(field);
  });

  it("starts the next rename from the saved name", async () => {
    updatePeopleTag.mockRejectedValue(
      httpError(400, "Tag with this name already exists"),
    );
    await renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Rename Clients" }));
    const field = screen.getByDisplayValue("Clients");
    fireEvent.change(field, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Rename Clients" }));

    expect(screen.getByDisplayValue("Clients")).toBeTruthy();
  });

  it("keeps a refused sub-tag name in its field", async () => {
    createPeopleTag.mockRejectedValue(httpError(400, "Tag already exists"));
    await renderSidebar();

    fireEvent.click(
      screen.getByRole("button", { name: "Add sub-tag to Clients" }),
    );
    const field = screen.getByPlaceholderText("New sub-tag...");
    fireEvent.change(field, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });

    expect(createPeopleTag).toHaveBeenCalledWith(
      "Suppliers",
      expect.any(String),
      1,
    );
    expect(messages()).toEqual(["error: Tag already exists"]);
    expect(screen.getByPlaceholderText("New sub-tag...")).toHaveValue(
      "Suppliers",
    );
  });

  it("opens the next sub-tag field empty after a refused name", async () => {
    createPeopleTag.mockRejectedValue(httpError(400, "Tag already exists"));
    await renderSidebar();

    fireEvent.click(
      screen.getByRole("button", { name: "Add sub-tag to Clients" }),
    );
    const field = screen.getByPlaceholderText("New sub-tag...");
    fireEvent.change(field, { target: { value: "Suppliers" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.click(
      screen.getByRole("button", { name: "Add sub-tag to Clients" }),
    );

    expect(screen.getByPlaceholderText("New sub-tag...")).toHaveValue("");
  });

  it("clears the sub-tag field once the sub-tag is created", async () => {
    createPeopleTag.mockResolvedValue({ id: 3, name: "Partners", parent_id: 1 });
    await renderSidebar();

    fireEvent.click(
      screen.getByRole("button", { name: "Add sub-tag to Clients" }),
    );
    const field = screen.getByPlaceholderText("New sub-tag...");
    fireEvent.change(field, { target: { value: "Partners" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });

    await waitFor(() =>
      expect(screen.queryByPlaceholderText("New sub-tag...")).toBeNull(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add sub-tag to Clients" }),
    );
    expect(screen.getByPlaceholderText("New sub-tag...")).toHaveValue("");
    expect(messages()).toEqual([]);
  });

  it("says why a delete failed", async () => {
    deletePeopleTag.mockRejectedValue(httpError(404, "Tag not found"));
    await renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Delete Suppliers" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    });

    expect(deletePeopleTag).toHaveBeenCalledWith(2);
    expect(messages()).toEqual(["error: Tag not found"]);
  });
});
