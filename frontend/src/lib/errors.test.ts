import { describe, expect, it } from "vitest";

import {
  describeErrorDetail,
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

// FastAPI answers a request it cannot validate with a 422 whose detail is a
// list, and some routes refuse with a coded object (#1008).
describe("describeErrorDetail", () => {
  it("reads a request validation list as its messages, each after its field", () => {
    expect(
      describeErrorDetail([
        { loc: ["query", "max_speakers"], msg: "Input should be less than or equal to 50", type: "less_than_equal" },
        { loc: ["body"], msg: "Field required", type: "missing" },
      ]),
    ).toBe("max speakers: Input should be less than or equal to 50 body: Field required");
    expect(describeErrorDetail([{ loc: [0], msg: "Bad entry" }])).toBe("Bad entry");
  });

  it("reads a coded refusal as its message", () => {
    expect(
      describeErrorDetail({ code: "active_recording_exists", message: "Finish the recording first." }),
    ).toBe("Finish the recording first.");
  });

  it("finds no text in a detail that has none", () => {
    expect(describeErrorDetail([{ loc: ["query", "max_speakers"], type: "missing" }])).toBeNull();
    expect(describeErrorDetail({ code: "example" })).toBeNull();
    expect(describeErrorDetail([])).toBeNull();
    expect(describeErrorDetail("  ")).toBeNull();
    expect(describeErrorDetail(42)).toBeNull();
  });
});

describe("getErrorMessage with a detail that is not text", () => {
  const failed = (detail: unknown) =>
    Object.assign(new Error("Request failed with status code 422"), {
      response: { status: 422, data: { detail } },
    });

  it("gives readable text for a list or an object", () => {
    expect(
      getErrorMessage(failed([{ loc: ["query", "name"], msg: "String should have at most 255 characters" }]), "fallback"),
    ).toBe("name: String should have at most 255 characters");
    expect(getErrorMessage(failed({ code: "x", message: "Refused for a reason." }), "fallback"))
      .toBe("Refused for a reason.");
  });

  it("falls back as before when the detail holds no text", () => {
    expect(getErrorMessage(failed([{ type: "missing" }]), "fallback")).toBe(
      "Request failed with status code 422",
    );
    expect(getErrorMessage({ response: { data: { detail: { code: "x" } } } }, "fallback")).toBe(
      "fallback",
    );
  });
});

describe("isAbortError", () => {
  it("detects AbortError-shaped values", () => {
    expect(isAbortError({ name: "AbortError" })).toBe(true);
    expect(isAbortError(new Error("other"))).toBe(false);
  });
});
