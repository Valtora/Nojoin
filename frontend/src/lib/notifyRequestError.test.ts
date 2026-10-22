import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { httpError } from "@/test/httpErrors";

import { useNotificationStore } from "./notificationStore";
import { notifyRequestError } from "./notifyRequestError";

const shown = () =>
  useNotificationStore
    .getState()
    .activeNotifications.map(({ type, message }) => ({ type, message }));

describe("notifyRequestError", () => {
  beforeEach(() => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the reason the API gave for a refusal", () => {
    notifyRequestError(
      httpError(400, "Tag with this name already exists"),
      "Failed to rename tag.",
    );

    expect(shown()).toEqual([
      { type: "error", message: "Tag with this name already exists" },
    ]);
  });

  it("names the action when the response gives no reason", () => {
    notifyRequestError(httpError(500), "Failed to move tag.");
    notifyRequestError(new Error("Network Error"), "Failed to delete tag.");

    expect(shown().map(({ message }) => message)).toEqual([
      "Failed to move tag.",
      "Failed to delete tag.",
    ]);
  });

  it("logs the error", () => {
    const error = httpError(404, "Tag not found");
    notifyRequestError(error, "Failed to delete tag.");

    expect(console.error).toHaveBeenCalledWith("Failed to delete tag.", error);
  });
});
