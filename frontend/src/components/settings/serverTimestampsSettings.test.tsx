import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { UserRole, type CliOAuthStatus, type CliUsageRow, type Invitation } from "@/types";
import type { ConnectedApp, Document } from "@/lib/api";
import { setCachedUserTimeZone } from "@/lib/timezone";
import { withProcessTimeZone } from "@/test/processTimeZone";

const mocks = vi.hoisted(() => ({
  getInvitations: vi.fn(),
  getConnectedApps: vi.fn(),
  getDocuments: vi.fn(),
  getCliOAuthStatus: vi.fn(),
  getCliUsageOverview: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getInvitations: mocks.getInvitations,
  getConnectedApps: mocks.getConnectedApps,
  getDocuments: mocks.getDocuments,
}));
vi.mock("@/lib/api/cliOauth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/cliOauth")>()),
  getCliOAuthStatus: mocks.getCliOAuthStatus,
  getCliUsageOverview: mocks.getCliUsageOverview,
}));
vi.mock("../DocumentUploadModal", () => ({ default: () => null }));

import CliOAuthPanel from "./CliOAuthPanel";
import CliUsageTab from "./CliUsageTab";
import ConnectedAppsSettings from "./ConnectedAppsSettings";
import InvitesTab from "./InvitesTab";
import DocumentsView from "../DocumentsView";

// The browser's zone and the Nojoin setting differ, and every instant shown
// must be in the setting's zone.
describe.each([
  ["America/New_York", "Asia/Tokyo"],
  ["Asia/Tokyo", "America/New_York"],
])("server timestamps in settings and documents in %s with the setting in %s", (processZone, settingZone) => {
  withProcessTimeZone(processZone);

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2026, 9, 27, 12));
    setCachedUserTimeZone(settingZone);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Both sides of midnight matter: New York crosses the earlier UTC midnight,
  // while Tokyo crosses the later one.
  it.each(["2026-10-27T00:30:00", "2026-10-27T20:30:00"])(
    "shows the expiry date of the UTC invitation instant %s in the setting's zone",
    async (expiresAt) => {
      const invitation: Invitation = {
        id: 1, code: "invite", role: UserRole.USER, expires_at: expiresAt,
        max_uses: 1, used_count: 0, is_revoked: false, created_by_id: 1,
        link: "https://example.test/invite", users: [],
      };
      mocks.getInvitations.mockResolvedValue([invitation]);
      render(<InvitesTab />);

      const expectedDay = new Date(`${expiresAt}Z`).toLocaleDateString(undefined, { timeZone: settingZone });
      expect(await screen.findByText(`Used 0 of 1. Expires ${expectedDay}.`)).toBeVisible();
    },
  );

  it.each(["2026-10-27T00:30:00", "2026-10-27T20:30:00"])(
    "shows the upload date of the UTC document instant %s in the setting's zone",
    async (createdAt) => {
      const document: Document = {
        id: 1, recording_id: "rec-1", title: "Agenda.pdf", file_path: "agenda.pdf",
        file_type: "application/pdf", status: "READY", parse_mode: "STRUCTURAL",
        pages_parsed: 1, created_at: createdAt, updated_at: createdAt,
      };
      mocks.getDocuments.mockResolvedValue([document]);
      render(<DocumentsView recordingId="rec-1" />);

      const row = (await screen.findByText("Agenda.pdf")).closest("tr")!;
      const expectedDay = new Date(`${createdAt}Z`).toLocaleDateString(undefined, { timeZone: settingZone });
      expect(within(row).getByText(expectedDay)).toBeVisible();
    },
  );

  it("shows Unknown date for an unreadable invitation expiry and document date", async () => {
    mocks.getInvitations.mockResolvedValue([{
      id: 1, code: "invite", role: UserRole.USER, expires_at: "not-a-timestamp",
      max_uses: 1, used_count: 0, is_revoked: false, created_by_id: 1,
      link: "https://example.test/invite", users: [],
    } satisfies Invitation]);
    mocks.getDocuments.mockResolvedValue([{
      id: 1, recording_id: "rec-1", title: "Agenda.pdf", file_path: "agenda.pdf",
      file_type: "application/pdf", status: "READY", parse_mode: "STRUCTURAL",
      pages_parsed: 1, created_at: "not-a-timestamp", updated_at: "not-a-timestamp",
    } satisfies Document]);
    render(<InvitesTab />);
    render(<DocumentsView recordingId="rec-1" />);

    expect(await screen.findByText("Used 0 of 1. Expires Unknown date.")).toBeVisible();
    const row = (await screen.findByText("Agenda.pdf")).closest("tr")!;
    expect(within(row).getByText("Unknown date")).toBeVisible();
  });

  it("preserves both explicit offsets in connected-app connection and last-use clocks", async () => {
    const app: ConnectedApp = {
      grant_id: "grant", client_name: "Assistant", scope: "mcp:read",
      created_at: "2026-10-27T14:30:00+02:00", last_used_at: "2026-10-27T08:00:00-04:00",
    };
    mocks.getConnectedApps.mockResolvedValue([app]);
    render(<ConnectedAppsSettings />);

    const connected = new Date("2026-10-27T12:30:00Z").toLocaleString(undefined, { timeZone: settingZone });
    const used = new Date("2026-10-27T12:00:00Z").toLocaleString(undefined, { timeZone: settingZone });
    expect(await screen.findByText(`Read-only · Connected ${connected} · Last used ${used}`)).toBeVisible();
  });

  it("keeps the connected-app missing and invalid timestamp fallbacks", async () => {
    mocks.getConnectedApps.mockResolvedValue([{
      grant_id: "grant", client_name: "Assistant", scope: "mcp:read",
      created_at: "not-a-timestamp", last_used_at: null,
    } satisfies ConnectedApp]);
    render(<ConnectedAppsSettings />);

    expect(await screen.findByText("Read-only · Connected not-a-timestamp · Last used Never")).toBeVisible();
  });

  // The CLI parsers already correct naive UTC. These are preservation tests
  // for consolidating them, rather than claims of a failing baseline.
  it.each(["2026-10-27T12:30:00", "2026-10-27T12:30:00Z", "2026-10-27T14:30:00+02:00"])(
    "keeps the subscription rate limit and clock for %s",
    async (limitedUntil) => {
      const status: CliOAuthStatus = {
        providers: [{
          provider: "claude_code", connected: true, status: "active",
          usage_limited_until: limitedUntil,
        }],
      };
      mocks.getCliOAuthStatus.mockResolvedValue(status);
      render(<CliOAuthPanel />);

      expect(await screen.findByText("Rate-limited")).toBeVisible();
      const expectedTime = new Date("2026-10-27T12:30:00Z").toLocaleString(undefined, { timeZone: settingZone });
      expect(screen.getByText(`Limit reached until ${expectedTime}. The server's providers answer until then.`))
        .toBeVisible();
    },
  );

  it.each(["2026-10-27T12:30:00", "2026-10-27T12:30:00Z", "2026-10-27T14:30:00+02:00"])(
    "keeps the admin usage rate limit and clock for %s",
    async (limitedUntil) => {
      const row: CliUsageRow = {
        user_id: 1, username: "alice", connected: true, tokens_total: 10,
        tokens_7d: 10, tokens_30d: 10, requests_total: 1,
        usage_limited_until: limitedUntil, last_used_on: "2026-10-27",
      };
      mocks.getCliUsageOverview.mockResolvedValue({ items: [row], total: 1 });
      render(<CliUsageTab />);

      const expectedTime = new Date("2026-10-27T12:30:00Z").toLocaleString(undefined, { timeZone: settingZone });
      expect(await screen.findByText(`Limited until ${expectedTime}`)).toBeVisible();
      // The rollup is the civil date 27 October, whatever the browser's zone
      // and the setting.
      const lastUsed = new Date(2026, 9, 27).toLocaleDateString();
      expect(screen.getByText(`Last used ${lastUsed}`)).toBeVisible();
    },
  );
});
