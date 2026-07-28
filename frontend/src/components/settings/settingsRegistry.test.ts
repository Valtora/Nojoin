import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  LEGACY_SETTINGS_TAB_VALUES,
  SETTINGS_CATEGORIES,
  SETTINGS_CATEGORY_IDS,
  SETTINGS_GROUP_IDS,
  getSettingsNavGroups,
  resolveLegacySettingsTab,
} from "./settingsCategories";
import {
  ADVANCED_CRITERION_LABELS,
  SETTINGS_REGISTRY,
  UNSURFACED_SETTINGS_KEYS,
  categoryUsesAdvancedGate,
  countChangedAdvanced,
  getRegistryEntriesForCategory,
  partitionByAdvanced,
  searchSettingsRegistry,
} from "./settingsRegistry";

/**
 * Reads the field names declared on the `Settings` interface straight from the
 * type definition.
 *
 * `Settings` carries an index signature, so `keyof Settings` widens to `string`
 * and no type-level assertion can enforce coverage. Parsing the source is the
 * only way to make "every setting has a home" a real check rather than a
 * hand-maintained list that drifts.
 */
function readDeclaredSettingsKeys(): string[] {
  const source = readFileSync(
    path.resolve(__dirname, "../../types/index.ts"),
    "utf8",
  );

  const start = source.indexOf("export interface Settings {");
  expect(start, "Settings interface not found in types/index.ts").toBeGreaterThan(-1);

  const body = source.slice(start);
  const end = body.indexOf("[key: string]: unknown;");
  expect(end, "Settings index signature not found").toBeGreaterThan(-1);

  return Array.from(
    body.slice(0, end).matchAll(/^\s{2}(\w+)\??:/gm),
    (match) => match[1],
  );
}

const declaredKeys = readDeclaredSettingsKeys();

describe("settings registry parity", () => {
  it("finds a plausible number of declared Settings keys", () => {
    // Guards the parser itself: a regex that silently matches nothing would
    // make every coverage assertion below pass vacuously.
    expect(declaredKeys.length).toBeGreaterThan(40);
    expect(declaredKeys).toContain("enable_vad");
    expect(declaredKeys).toContain("secondary_llm_provider");
  });

  it("gives every declared Settings key exactly one home", () => {
    const owners = new Map<string, string[]>();

    for (const entry of SETTINGS_REGISTRY) {
      for (const key of entry.settingsKeys ?? []) {
        owners.set(key, [...(owners.get(key) ?? []), entry.id]);
      }
    }

    const unsurfaced = new Set<string>(UNSURFACED_SETTINGS_KEYS);
    const orphaned = declaredKeys.filter(
      (key) => !owners.has(key) && !unsurfaced.has(key),
    );

    expect(
      orphaned,
      "these Settings keys are registered nowhere and would render on no page",
    ).toEqual([]);
  });

  it("never registers the same Settings key twice", () => {
    const seen = new Map<string, string>();
    const duplicates: string[] = [];

    for (const entry of SETTINGS_REGISTRY) {
      for (const key of entry.settingsKeys ?? []) {
        const previous = seen.get(key);
        if (previous) {
          duplicates.push(`${key}: ${previous} and ${entry.id}`);
        }
        seen.set(key, entry.id);
      }
    }

    expect(duplicates).toEqual([]);
  });

  it("only registers keys that exist on the Settings interface", () => {
    const declared = new Set(declaredKeys);
    const unknown = SETTINGS_REGISTRY.flatMap((entry) =>
      (entry.settingsKeys ?? [])
        .filter((key) => !declared.has(key))
        .map((key) => `${entry.id} -> ${key}`),
    );

    expect(unknown).toEqual([]);
  });

  it("does not list a surfaced key as unsurfaced", () => {
    const registered = new Set(
      SETTINGS_REGISTRY.flatMap((entry) => entry.settingsKeys ?? []),
    );
    const contradictions = UNSURFACED_SETTINGS_KEYS.filter((key) =>
      registered.has(key),
    );

    expect(contradictions).toEqual([]);
  });

  it("keeps the unsurfaced list honest about the type", () => {
    const declared = new Set(declaredKeys);
    const stale = UNSURFACED_SETTINGS_KEYS.filter((key) => !declared.has(key));

    expect(
      stale,
      "these keys were removed from Settings and should leave the unsurfaced list",
    ).toEqual([]);
  });

  it("uses unique entry ids", () => {
    const ids = SETTINGS_REGISTRY.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("settings categories", () => {
  it("routes every registry entry to a known category", () => {
    const known = new Set<string>(SETTINGS_CATEGORY_IDS);
    const unknown = SETTINGS_REGISTRY.filter(
      (entry) => !known.has(entry.category),
    ).map((entry) => `${entry.id} -> ${entry.category}`);

    expect(unknown).toEqual([]);
  });

  it("gives every category at least one entry", () => {
    const empty = SETTINGS_CATEGORY_IDS.filter(
      (id) => !SETTINGS_REGISTRY.some((entry) => entry.category === id),
    );

    expect(empty, "a category with no entries renders an empty page").toEqual([]);
  });

  it("places every category in a known group", () => {
    const known = new Set<string>(SETTINGS_GROUP_IDS);
    const misplaced = SETTINGS_CATEGORY_IDS.filter(
      (id) => !known.has(SETTINGS_CATEGORIES[id].group),
    );

    expect(misplaced).toEqual([]);
  });

  it("never puts an all-access entry in an admin-only category", () => {
    // Otherwise the entry is invisible to the users it was written for.
    const stranded = SETTINGS_REGISTRY.filter(
      (entry) =>
        entry.access === "all" &&
        SETTINGS_CATEGORIES[entry.category].access === "admin",
    ).map((entry) => `${entry.id} in ${entry.category}`);

    expect(stranded).toEqual([]);
  });

  it("shows fewer categories to a non-admin than to an admin", () => {
    const adminCount = getSettingsNavGroups({ isAdmin: true }).flatMap(
      (group) => group.items,
    ).length;
    const userCount = getSettingsNavGroups({ isAdmin: false }).flatMap(
      (group) => group.items,
    ).length;

    expect(adminCount).toBe(SETTINGS_CATEGORY_IDS.length);
    expect(userCount).toBeLessThan(adminCount);
    expect(userCount).toBeGreaterThan(0);
  });

  it("drops groups that have no visible category", () => {
    for (const group of getSettingsNavGroups({ isAdmin: false })) {
      expect(group.items.length).toBeGreaterThan(0);
    }
  });

  it("locks navigation to Profile during a forced password change", () => {
    const groups = getSettingsNavGroups({
      isAdmin: true,
      forcePasswordChange: true,
    });
    const items = groups.flatMap((group) => group.items);

    expect(items.map((item) => item.id)).toEqual(["profile"]);
  });

  it("resolves every legacy ?tab= value to a real category", () => {
    for (const legacy of LEGACY_SETTINGS_TAB_VALUES) {
      const resolved = resolveLegacySettingsTab(legacy);
      expect(resolved, `legacy tab "${legacy}" resolves nowhere`).not.toBeNull();
      expect(SETTINGS_CATEGORY_IDS).toContain(resolved);
    }
  });

  it("passes through current category ids and rejects unknown ones", () => {
    expect(resolveLegacySettingsTab("recording")).toBe("recording");
    expect(resolveLegacySettingsTab("nonsense")).toBeNull();
    expect(resolveLegacySettingsTab(null)).toBeNull();
  });
});

describe("advanced gate", () => {
  it("gives every gated entry a criterion with a label", () => {
    for (const entry of SETTINGS_REGISTRY) {
      if (!entry.advanced) continue;
      expect(
        ADVANCED_CRITERION_LABELS[entry.advanced],
        `${entry.id} cites an unknown criterion`,
      ).toBeTruthy();
    }
  });

  it("never leaves a category with nothing visible", () => {
    for (const isAdmin of [true, false]) {
      for (const id of SETTINGS_CATEGORY_IDS) {
        const entries = getRegistryEntriesForCategory(id, { isAdmin });
        if (entries.length === 0) continue;

        const { standard } = partitionByAdvanced(id, { isAdmin });
        expect(
          standard.length,
          `${id} would hide all of its content behind Advanced (isAdmin=${isAdmin})`,
        ).toBeGreaterThan(0);
      }
    }
  });

  it("suppresses the gate when everything in a category is advanced", () => {
    // The floor rule: rather than an all-collapsed page, show everything.
    const fullyGated = SETTINGS_CATEGORY_IDS.filter((id) => {
      const entries = getRegistryEntriesForCategory(id, { isAdmin: true });
      return entries.length > 0 && entries.every((entry) => entry.advanced);
    });

    for (const id of fullyGated) {
      expect(categoryUsesAdvancedGate(id, { isAdmin: true })).toBe(false);
    }
  });

  it("gates Recording but not Profile", () => {
    expect(categoryUsesAdvancedGate("recording", { isAdmin: false })).toBe(true);
    expect(categoryUsesAdvancedGate("profile", { isAdmin: false })).toBe(false);
  });
});

describe("changed-count badge", () => {
  it("counts nothing when values match their defaults", () => {
    expect(
      countChangedAdvanced(
        "recording",
        { enable_vad: true, enable_diarization: true },
        { isAdmin: false },
      ),
    ).toBe(0);
  });

  it("counts a gated setting that differs from its default", () => {
    expect(
      countChangedAdvanced(
        "recording",
        { enable_vad: false, enable_diarization: true },
        { isAdmin: false },
      ),
    ).toBe(1);
  });

  it("ignores unset values rather than counting them as changed", () => {
    expect(countChangedAdvanced("recording", {}, { isAdmin: false })).toBe(0);
  });
});

describe("registry search", () => {
  it("returns nothing for an empty query", () => {
    expect(searchSettingsRegistry("", { isAdmin: true })).toEqual([]);
    expect(searchSettingsRegistry("   ", { isAdmin: true })).toEqual([]);
  });

  it("finds settings across categories, not just categories", () => {
    const results = searchSettingsRegistry("gain", { isAdmin: false });
    const ids = results.map((result) => result.entry.id);

    expect(ids).toContain("recording-microphone-gain");
    expect(ids).toContain("recording-shared-audio-gain");
  });

  it("reaches a setting that lives behind the Advanced gate", () => {
    const ids = searchSettingsRegistry("echo", { isAdmin: false }).map(
      (result) => result.entry.id,
    );

    expect(ids).toContain("recording-echo-cancellation");
  });

  it("hides admin-only settings from a non-admin", () => {
    const adminIds = searchSettingsRegistry("hugging face", {
      isAdmin: true,
    }).map((result) => result.entry.id);
    const userIds = searchSettingsRegistry("hugging face", {
      isAdmin: false,
    }).map((result) => result.entry.id);

    expect(adminIds).toContain("ai-providers-hf-token");
    expect(userIds).not.toContain("ai-providers-hf-token");
  });

  it("orders better matches first", () => {
    const results = searchSettingsRegistry("password", { isAdmin: false });

    expect(results.length).toBeGreaterThan(0);
    expect(results[0].entry.id).toBe("profile-password");
  });

  it("finds settings by their old names", () => {
    // The categories were renamed; the words users know were kept as keywords.
    const byOldName = (query: string) =>
      searchSettingsRegistry(query, { isAdmin: true }).map(
        (result) => result.entry.category,
      );

    expect(byOldName("capture")).toContain("recording");
    expect(byOldName("glossary")).toContain("transcription");
    expect(byOldName("invite")).toContain("users");
  });
});
