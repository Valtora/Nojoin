import { describe, expect, it } from "vitest";

import { RETENTION_DAYS, retentionCutoff, rollupStatements } from "../src/rollup";

describe("retentionCutoff", () => {
  it("is 13 months back from now", () => {
    expect(RETENTION_DAYS).toBe(395);
    expect(retentionCutoff(Date.UTC(2026, 6, 25))).toBe("2025-06-25");
  });

  it("accepts an explicit retention for testing without touching the default", () => {
    expect(retentionCutoff(Date.UTC(2026, 6, 25), 1)).toBe("2026-07-24");
  });
});

describe("rollupStatements", () => {
  const statements = rollupStatements();

  it("aggregates before deleting, so no raw row is dropped unaggregated", () => {
    const deleteIndex = statements.findIndex((sql) => sql.startsWith("DELETE"));
    expect(deleteIndex).toBe(statements.length - 1);
    expect(statements.slice(0, deleteIndex).every((sql) => sql.includes("INSERT OR REPLACE"))).toBe(
      true,
    );
  });

  it("binds the cutoff in every statement, so none can run unbounded", () => {
    expect(statements.every((sql) => sql.includes("?1"))).toBe(true);
    expect(statements.every((sql) => sql.includes("day < ?1"))).toBe(true);
  });

  it("is idempotent, so a retried sweep recomputes rather than doubles", () => {
    expect(statements.filter((sql) => sql.startsWith("INSERT")).every((sql) =>
      sql.startsWith("INSERT OR REPLACE"),
    )).toBe(true);
  });

  it("preserves every breakdown dimension the raw rows carried", () => {
    const dimensions = [
      "version",
      "llm_provider",
      "asr_engine",
      "whisper_model_size",
      "local_origin",
      "gpu",
      "secondary_configured",
      "cli_oauth_in_use",
      "meeting_edge_enabled",
      "calendar_connected",
      "mcp_in_use",
      "chat_used_28d",
      "documents_used",
      "tasks_used",
      "people_library_used",
    ];
    for (const dimension of dimensions) {
      expect(statements.some((sql) => sql.includes(`'${dimension}'`))).toBe(true);
    }
  });

  it("keeps an unreported boolean distinct from a reported false", () => {
    const booleanStatement = statements.find((sql) => sql.includes("'gpu'"));
    expect(booleanStatement).toContain("IS NULL THEN 'unknown'");
    expect(booleanStatement).toContain("= 1 THEN 'true'");
  });
});
