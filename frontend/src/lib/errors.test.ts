import { describe, expect, it } from "vitest";

import {
  getErrorDetail,
  getErrorMessage,
  getErrorStatus,
  isAbortError,
  isApiErrorPayload,
  isRecord,
} from "./errors";

describe("isRecord", () => {
  it("accepts plain objects", () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord({ a: 1 })).toBe(true);
  });

  it("rejects null, arrays, and primitives", () => {
    expect(isRecord(null)).toBe(false);
    expect(isRecord([1, 2])).toBe(false);
    expect(isRecord("x")).toBe(false);
    expect(isRecord(42)).toBe(false);
    expect(isRecord(undefined)).toBe(false);
  });
});

describe("isApiErrorPayload", () => {
  it("narrows record-shaped bodies", () => {
    expect(isApiErrorPayload({ detail: "boom" })).toBe(true);
    expect(isApiErrorPayload({})).toBe(true);
  });

  it("rejects non-objects", () => {
    expect(isApiErrorPayload("boom")).toBe(false);
    expect(isApiErrorPayload(null)).toBe(false);
  });
});

describe("getErrorStatus", () => {
  it("reads status from an error-like response", () => {
    expect(getErrorStatus({ response: { status: 404 } })).toBe(404);
  });

  it("returns null when no status is present", () => {
    expect(getErrorStatus(new Error("nope"))).toBeNull();
    expect(getErrorStatus("nope")).toBeNull();
  });
});

describe("getErrorDetail / getErrorMessage", () => {
  it("extracts a non-empty string detail", () => {
    const error = { response: { data: { detail: "Recording not found" } } };
    expect(getErrorDetail(error)).toBe("Recording not found");
    expect(getErrorMessage(error, "fallback")).toBe("Recording not found");
  });

  it("falls back to Error.message then the fallback", () => {
    expect(getErrorMessage(new Error("boom"), "fallback")).toBe("boom");
    expect(getErrorMessage({}, "fallback")).toBe("fallback");
  });
});

describe("isAbortError", () => {
  it("detects AbortError-shaped values", () => {
    expect(isAbortError({ name: "AbortError" })).toBe(true);
    expect(isAbortError(new Error("other"))).toBe(false);
  });
});
