import { describe, expect, it } from "vitest";

import { dayBucket, parsePayload, type TelemetryRow } from "../src/payload";

const VALID = {
  schema: 1,
  install_id: "3F2A5C1E-9B4D-4A7F-8E1C-2D6B0A9F4E33",
  version: "1.6.0",
  install_age_days: 42,
  local_origin: false,
  users_total: 4,
  users_recording_28d: 3,
  recordings_total: 271,
  recordings_28d: 19,
  recording_hours_28d: 15.6789,
  llm_provider: "anthropic",
  secondary_configured: true,
  cli_oauth_in_use: false,
  meeting_edge_enabled: true,
  asr_engine: "whisper",
  whisper_model_size: "turbo",
  gpu: true,
  calendar_connected: true,
  mcp_in_use: false,
  chat_used_28d: true,
  documents_used: true,
  tasks_used: true,
  people_library_used: true,
};

function parseValid(overrides: Record<string, unknown> = {}): TelemetryRow {
  const result = parsePayload({ ...VALID, ...overrides });
  if (!result.ok) {
    throw new Error(`expected payload to parse, got: ${result.error}`);
  }
  return result.row;
}

describe("parsePayload rejection", () => {
  it.each([
    ["a string body", "nope"],
    ["an array body", [1, 2, 3]],
    ["null", null],
  ])("rejects %s", (_label, body) => {
    expect(parsePayload(body).ok).toBe(false);
  });

  it("rejects an unsupported schema version", () => {
    expect(parsePayload({ ...VALID, schema: 99 }).ok).toBe(false);
    expect(parsePayload({ ...VALID, schema: "1" }).ok).toBe(false);
  });

  it("rejects a malformed install_id, since it is the dedupe key", () => {
    for (const bad of ["", "not-a-uuid", 42, null, "3f2a5c1e9b4d4a7f8e1c2d6b0a9f4e33"]) {
      expect(parsePayload({ ...VALID, install_id: bad }).ok).toBe(false);
    }
  });
});

describe("parsePayload normalisation", () => {
  it("lowercases the install id so casing cannot split one install in two", () => {
    expect(parseValid().install_id).toBe("3f2a5c1e-9b4d-4a7f-8e1c-2d6b0a9f4e33");
  });

  it("stores booleans as 1/0 and non-booleans as null", () => {
    expect(parseValid({ gpu: true }).gpu).toBe(1);
    expect(parseValid({ gpu: false }).gpu).toBe(0);
    expect(parseValid({ gpu: "yes" }).gpu).toBeNull();
    expect(parseValid({ gpu: 1 }).gpu).toBeNull();
  });

  it("rounds recording hours to one decimal place", () => {
    expect(parseValid({ recording_hours_28d: 15.6789 }).recording_hours_28d).toBe(15.7);
  });

  it("nulls counters that are negative, absurd, or not numbers", () => {
    expect(parseValid({ users_total: -1 }).users_total).toBeNull();
    expect(parseValid({ users_total: 10 ** 9 }).users_total).toBeNull();
    expect(parseValid({ users_total: "many" }).users_total).toBeNull();
    expect(parseValid({ recordings_28d: Number.NaN }).recordings_28d).toBeNull();
  });

  it("truncates a fractional counter rather than storing it as a float", () => {
    expect(parseValid({ users_total: 4.9 }).users_total).toBe(4);
  });

  it("rejects a malformed version string into null without failing the ping", () => {
    expect(parseValid({ version: "1.6.0" }).version).toBe("1.6.0");
    expect(parseValid({ version: "x".repeat(40) }).version).toBeNull();
    expect(parseValid({ version: "1.6.0; DROP TABLE pings" }).version).toBeNull();
  });
});

describe("forward compatibility", () => {
  it("accepts unknown fields and preserves them in the raw payload", () => {
    const row = parseValid({ some_future_metric: 7, another: { nested: true } });
    const stored = JSON.parse(row.payload);
    expect(stored.some_future_metric).toBe(7);
    expect(stored.another).toEqual({ nested: true });
  });

  it("stores an unknown enum value as null but keeps the real value in the payload", () => {
    const row = parseValid({ llm_provider: "mistral" });
    expect(row.llm_provider).toBeNull();
    expect(JSON.parse(row.payload).llm_provider).toBe("mistral");
  });
});

describe("row shape", () => {
  // Locks the stored surface. If a future change starts persisting something
  // new, this fails and forces the docs/TELEMETRY.md disclosure to be updated
  // in the same change rather than silently drifting out of date.
  it("stores exactly the documented columns and nothing else", () => {
    expect(Object.keys(parseValid()).sort()).toEqual(
      [
        "asr_engine",
        "calendar_connected",
        "chat_used_28d",
        "cli_oauth_in_use",
        "documents_used",
        "gpu",
        "install_age_days",
        "install_id",
        "llm_provider",
        "local_origin",
        "mcp_in_use",
        "meeting_edge_enabled",
        "payload",
        "people_library_used",
        "recording_hours_28d",
        "recordings_28d",
        "recordings_total",
        "schema_version",
        "secondary_configured",
        "tasks_used",
        "users_recording_28d",
        "users_total",
        "version",
        "whisper_model_size",
      ].sort(),
    );
  });
});

describe("dayBucket", () => {
  it("derives a UTC day from server time", () => {
    expect(dayBucket(Date.UTC(2026, 6, 25, 13, 45, 0))).toBe("2026-07-25");
  });

  it("uses the boundary in UTC, not local time", () => {
    expect(dayBucket(Date.UTC(2026, 6, 25, 23, 59, 59))).toBe("2026-07-25");
    expect(dayBucket(Date.UTC(2026, 6, 26, 0, 0, 0))).toBe("2026-07-26");
  });
});
