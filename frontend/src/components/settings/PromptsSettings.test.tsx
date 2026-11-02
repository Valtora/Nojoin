import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from "axios";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api from "@/lib/api/client";
import { UserRole, type PromptCatalogueEntry } from "@/types";
import SettingsProvider, { useSettingsContext } from "./SettingsProvider";
import SettingsShell from "./SettingsShell";
import PromptsSettings from "./PromptsSettings";
import { getSettingsNavGroups } from "./settingsCategories";
import { searchSettingsRegistry } from "./settingsRegistry";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/prompts",
  useRouter: () => ({ replace }),
}));

const originalAdapter = api.defaults.adapter;
let developerMode: boolean;
let role: UserRole;
let superuser: boolean;
let capabilityFails: boolean;
let catalogueFails: boolean;
let saveFails: boolean;
let requests: InternalAxiosRequestConfig[];
const invalid: PromptCatalogueEntry = {
  id: "shared.calendar_title", feature: "shared", placeholders: ["title"], required_literals: [],
  default_text: "Meeting title: ${title}", override_text: "Broken override", status: "invalid",
};
const changed: PromptCatalogueEntry = {
  ...invalid, id: "structure.instructions", feature: "structure", default_text: "New notes default",
  placeholders: ["max_notes_template_name_length", "max_notes_template_description_length", "max_notes_sections_length"],
  required_literals: ['"name"', '"description"', '"sections"'],
  override_text: "Old notes override", status: "default changed",
};

function refuse(config: InternalAxiosRequestConfig) {
  const response = { data: { detail: "Keep ${title}." }, status: 422,
    statusText: "Unprocessable Entity", headers: new AxiosHeaders(), config };
  throw new AxiosError("Refused", "ERR_BAD_REQUEST", config, undefined, response);
}

function Probe() {
  const { viewer, settings, loading } = useSettingsContext();
  return <output>{loading ? "loading" : `${viewer.developerMode}:${settings.whisper_model_size}`}</output>;
}

function openSettings() {
  return render(<SettingsProvider><SettingsShell><PromptsSettings /></SettingsShell></SettingsProvider>);
}

describe("developer prompts", () => {
  beforeEach(() => {
    developerMode = true;
    role = UserRole.ADMIN;
    superuser = false;
    capabilityFails = false;
    catalogueFails = false;
    saveFails = false;
    requests = [];
    replace.mockClear();
    api.defaults.adapter = async (config) => {
      requests.push(config);
      let data: unknown;
      if (config.url === "/users/me") data = {
        id: 1, username: "Mike", role, is_superuser: superuser, force_password_change: false,
      };
      else if (config.url === "/settings") data = { whisper_model_size: "small" };
      else if (config.url === "/system/capabilities") {
        if (capabilityFails) refuse(config);
        data = { developer_mode: developerMode };
      } else if (config.url === "/version") data = {};
      else if (config.url === "/settings/prompts") {
        if (catalogueFails) refuse(config);
        data = [invalid, changed];
      } else if (config.url === "/settings/prompts/shared.calendar_title") {
        if (saveFails) refuse(config);
        data = config.method === "delete"
          ? { ...invalid, override_text: null, status: "default" }
          : { ...invalid, override_text: JSON.parse(config.data).text, status: "overridden" };
      } else throw new Error(`Unexpected request ${config.method} ${config.url}`);
      return { data, status: 200, statusText: "OK", headers: new AxiosHeaders(), config };
    };
  });

  afterEach(() => { api.defaults.adapter = originalAdapter; });

  it("gates navigation, search and direct routes by both role and capability", async () => {
    for (const [accountRole, isSuperuser, enabled] of [
      [UserRole.ADMIN, false, false], [UserRole.USER, false, true],
      [UserRole.OWNER, false, true], [UserRole.USER, true, true],
    ] as const) {
      role = accountRole; superuser = isSuperuser; developerMode = enabled;
      requests = []; replace.mockClear();
      const permitted = enabled && (isSuperuser || accountRole === UserRole.OWNER);
      const viewer = { isAdmin: isSuperuser || accountRole !== UserRole.USER,
        isSuperuser, developerMode: enabled };
      expect(getSettingsNavGroups(viewer).some((group) => group.id === "developer")).toBe(permitted);
      expect(searchSettingsRegistry("prompts", viewer).some(({ entry }) => entry.id === "developer-prompts"))
        .toBe(permitted);
      const view = openSettings();
      if (permitted) {
        await screen.findByDisplayValue("Broken override");
        expect(replace).not.toHaveBeenCalled();
      } else {
        await waitFor(() => expect(replace).toHaveBeenCalledWith("/settings"));
        expect(screen.queryByDisplayValue("Broken override")).not.toBeInTheDocument();
        expect(requests.some((request) => request.url === "/settings/prompts")).toBe(false);
      }
      view.unmount();
    }
  });

  it("keeps ordinary settings available if capabilities cannot be loaded", async () => {
    capabilityFails = true;
    render(<SettingsProvider><Probe /><PromptsSettings /></SettingsProvider>);
    expect(await screen.findByText("false:small")).toBeInTheDocument();
    expect(requests.some((request) => request.url === "/settings/prompts")).toBe(false);
  });

  it("shows defaults and tokens, retains rejected drafts, saves and restores using authoritative responses", async () => {
    openSettings();
    const chat = await screen.findByRole("region", { name: "shared" });
    const notes = screen.getByRole("region", { name: "structure" });
    expect(within(chat).getByText("Invalid override · Default used")).toBeInTheDocument();
    expect(within(notes).getByText("Default changed")).toBeInTheDocument();
    expect(within(notes).getByRole("button", { name: "Save override" })).toBeEnabled();
    expect(within(notes).getByLabelText("Shipped default")).toHaveValue("New notes default");
    expect(within(chat).getByLabelText("Shipped default")).toHaveAttribute("readonly");
    expect(within(chat).getByText("Placeholders: ${title}")).toBeInTheDocument();
    expect(within(notes).getByText('Required literals: "name", "description", "sections"')).toBeInTheDocument();
    fireEvent.change(within(notes).getByLabelText("Override"), { target: { value: "Notes draft" } });
    const editor = within(chat).getByLabelText("Override");
    fireEvent.change(editor, { target: { value: "Updated ${title}" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    saveFails = true;
    fireEvent.click(within(chat).getByRole("button", { name: "Save override" }));
    expect(await within(chat).findByRole("alert")).toHaveTextContent("Keep ${title}.");
    expect(editor).toHaveValue("Updated ${title}");
    saveFails = false;
    fireEvent.click(within(chat).getByRole("button", { name: "Save override" }));
    await within(chat).findByText("Overridden");
    expect(JSON.parse(requests.findLast((request) => request.method === "put")!.data))
      .toEqual({ text: "Updated ${title}" });
    fireEvent.click(within(chat).getByRole("button", { name: "Restore default" }));
    await waitFor(() => expect(editor).toHaveValue(invalid.default_text));
    expect(within(chat).getByRole("button", { name: "Restore default" })).toBeDisabled();
    expect(within(chat).getByText("Default", { exact: true })).toBeInTheDocument();
    expect(requests.some((request) => request.method === "delete")).toBe(true);
    expect(within(notes).getByLabelText("Override")).toHaveValue("Notes draft");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(requests.some((request) => request.url === "/settings" && request.method !== "get")).toBe(false);
  });

  it("recovers from a catalogue failure when Retry is selected", async () => {
    catalogueFails = true;
    openSettings();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load prompts");
    catalogueFails = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByDisplayValue("Broken override")).toBeInTheDocument();
  });
});
