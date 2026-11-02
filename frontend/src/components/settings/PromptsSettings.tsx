"use client";

import { useEffect, useId, useState } from "react";
import { isAxiosError } from "axios";
import { getPrompts, restorePrompt, savePrompt } from "@/lib/api";
import type { PromptCatalogueEntry } from "@/types";
import Button from "@/components/ui/Button";
import { TextareaBase } from "@/components/ui/nativeBases";
import SettingsCard from "./SettingsCard";
import SettingsBlock from "./SettingsBlock";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { useSettingsContext, useSettingsDraft } from "./SettingsProvider";
import { canViewSettings } from "./settingsCategories";
import { SETTINGS_TEXTAREA_CLASS } from "./settingsControls";

function errorMessage(error: unknown): string {
  const detail = isAxiosError(error) ? error.response?.data?.detail : null;
  return typeof detail === "string" ? detail : "Could not update the prompt. Please try again.";
}

function PromptEditor({ entry, onSaved }: {
  entry: PromptCatalogueEntry;
  onSaved: (entry: PromptCatalogueEntry) => void;
}) {
  const id = useId();
  const [text, setText] = useState(entry.override_text ?? entry.default_text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const dirty = text !== (entry.override_text ?? entry.default_text);
  useSettingsDraft(`prompt:${entry.id}`, dirty);

  const persist = async (restore: boolean) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const updated = restore ? await restorePrompt(entry.id) : await savePrompt(entry.id, text);
      onSaved(updated);
      setText(updated.override_text ?? updated.default_text);
      setSaved(true);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const warning = entry.status === "invalid" || entry.status === "default changed";
  const status = entry.status === "invalid" ? "Invalid override · Default used"
    : entry.status === "default changed" ? "Default changed" : entry.status === "overridden" ? "Overridden" : "Default";

  return (
    <SettingsCard title={entry.id} badge={<SettingsStatusBadge tone={warning ? "warning" : "neutral"}>
      {status}
    </SettingsStatusBadge>}>
      <SettingsBlock contentClassName="space-y-4">
        {entry.status === "default changed" && <p className="text-sm contrast-helper">
          The shipped default has changed. Your override is still used; compare it with the current default.
        </p>}
        {entry.status === "invalid" && <p className="text-sm contrast-helper">
          The saved override is invalid. The shipped default is used until you save a valid override or restore it.
        </p>}
        <div className="grid gap-4 xl:grid-cols-2">
          <label className="min-w-0 space-y-2 text-sm font-medium" htmlFor={`${id}-default`}>
            <span>Shipped default</span>
            <TextareaBase id={`${id}-default`} readOnly value={entry.default_text}
              className={`${SETTINGS_TEXTAREA_CLASS} min-h-64 font-mono`} />
          </label>
          <label className="min-w-0 space-y-2 text-sm font-medium" htmlFor={`${id}-override`}>
            <span>Override</span>
            <TextareaBase id={`${id}-override`} value={text} disabled={busy}
              className={`${SETTINGS_TEXTAREA_CLASS} min-h-64 font-mono`}
              onChange={(event) => { setText(event.target.value); setSaved(false); }} />
          </label>
        </div>
        <div className="space-y-1 text-sm contrast-helper">
          <p>Placeholders: {entry.placeholders.length
            ? entry.placeholders.map((name) => `\${${name}}`).join(", ") : "None"}</p>
          <p>Required literals: {entry.required_literals.length ? entry.required_literals.join(", ") : "None"}</p>
          <p>Keep these tokens exactly as shown.</p>
        </div>
        {error && <p role="alert" className="text-sm text-status-danger-fg">{error}</p>}
        {saved && <p role="status" className="text-sm contrast-helper">Prompt saved.</p>}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy || (!dirty && entry.status !== "default changed")}
            onClick={() => void persist(false)}>Save override</Button>
          <Button disabled={busy || entry.override_text === null} onClick={() => void persist(true)}>Restore default</Button>
        </div>
      </SettingsBlock>
    </SettingsCard>
  );
}

export default function PromptsSettings() {
  const { viewer } = useSettingsContext();
  const allowed = canViewSettings("developer", viewer);
  const [entries, setEntries] = useState<PromptCatalogueEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!allowed) return;
    let active = true;
    getPrompts().then((entries) => {
      if (active) { setEntries(entries); setError(""); }
    }).catch(() => {
      if (active) setError("Could not load prompts. Please try again.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [allowed, attempt]);

  if (!allowed) return null;
  if (loading) return <p role="status" className="contrast-helper">Loading prompts...</p>;
  if (error) return <div><p role="alert">{error}</p>
    <Button onClick={() => setAttempt(attempt + 1)}>Retry</Button></div>;

  const features = [...new Set(entries.map((entry) => entry.feature))];
  return <div id="developer-prompts" className="space-y-6">
    {entries.length === 0 && <p className="contrast-helper">No prompts are available.</p>}
    {features.map((feature) => <section key={feature} aria-label={feature} className="space-y-4">
      <h2 className="text-lg font-semibold text-foreground">{feature}</h2>
      {entries.filter((entry) => entry.feature === feature).map((entry) =>
        <PromptEditor key={entry.id} entry={entry} onSaved={(updated) => {
          setEntries((previous) => previous.map((item) => item.id === updated.id ? updated : item));
        }} />)}
    </section>)}
  </div>;
}
