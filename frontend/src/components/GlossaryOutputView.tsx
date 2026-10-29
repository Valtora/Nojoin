"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { getSettings, updateSettings } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { DEMO_MODE } from "@/lib/demo";
import Button from "./ui/Button";
import { InputBase } from "./ui/nativeBases";

interface Props {
  notes: string | null;
  isGenerating: boolean;
  onRegenerate: () => void;
  onExtractMore: () => void;
  toolbarActions?: ReactNode;
}

/** Match current entries and the older bullet, equals and heard-as glossary formats. */
function termKey(line: string): string {
  const entry = line.trim().replace(/^[-*+]\s+/, "");
  const positions = [entry.indexOf(":"), entry.indexOf("="), entry.toLowerCase().indexOf(", heard as ")]
    .filter((index) => index >= 0);
  const end = positions.length ? Math.min(...positions) : entry.length;
  return entry.slice(0, end).trim().toLowerCase();
}

function suggestions(notes: string | null): string[] {
  const seen = new Set<string>();
  return (notes ?? "").split("\n").map((line) => line.trim()).filter((line) => {
    const key = termKey(line);
    if (!key || !line.includes(":") || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Checklist of generated terms; membership is saved through the existing settings API. */
export default function GlossaryOutputView({
  notes, isGenerating, onRegenerate, onExtractMore, toolbarActions,
}: Props) {
  const [glossary, setGlossary] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const savingRef = useRef(false);
  const entries = suggestions(notes);
  const personalTerms = new Set((glossary ?? "").split("\n").map(termKey));

  useEffect(() => {
    let cancelled = false;
    void getSettings().then((settings) => {
      if (!cancelled) setGlossary(settings.glossary_terms ?? "");
    }).catch((cause) => {
      if (!cancelled) setError(getErrorMessage(cause, "Could not load your glossary."));
    });
    return () => { cancelled = true; };
  }, []);

  const toggle = async (entry: string, checked: boolean) => {
    if (savingRef.current || DEMO_MODE) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    try {
      // Read before each mutation so a change made in Settings is preserved.
      const fresh = (await getSettings()).glossary_terms ?? "";
      setGlossary(fresh);
      const lines = fresh.split("\n");
      const key = termKey(entry);
      const exists = lines.some((line) => termKey(line) === key);
      const next = checked
        ? exists ? fresh : [fresh.trimEnd(), entry].filter(Boolean).join("\n")
        : lines.filter((line) => termKey(line) !== key).join("\n");
      const saved = await updateSettings({ glossary_terms: next });
      setGlossary(saved.glossary_terms ?? next);
    } catch (cause) {
      setError(getErrorMessage(cause, "Could not save your glossary."));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex flex-wrap items-center gap-2 border-b border-control-border p-3">
      <Button size="sm" disabled={isGenerating || saving} onClick={onExtractMore}>Extract more</Button>
      <Button size="sm" disabled={isGenerating || saving} onClick={onRegenerate}>Regenerate suggestions</Button>
      {toolbarActions}
    </div>
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <p className="text-sm text-contrast-helper">
        Check a term to add it to your personal glossary. Uncheck it to remove it. Shared terms are kept separately.
      </p>
      {error && <p role="alert" className="text-sm text-status-danger-fg">{error}</p>}
      {(isGenerating || saving || (glossary === null && !error)) && <p role="status" className="text-sm text-contrast-helper">
        {isGenerating ? "Extracting glossary suggestions…" : saving ? "Saving your glossary…" : "Loading your glossary…"}
      </p>}
      {!isGenerating && !entries.length && <p className="text-sm text-contrast-helper">No glossary suggestions yet.</p>}
      <ul className="space-y-2">
        {entries.map((entry) => <li key={termKey(entry)}>
          <label className="flex cursor-pointer items-start gap-3 rounded-lg p-2 hover:bg-surface-inset">
            <InputBase type="checkbox" checked={personalTerms.has(termKey(entry))}
              disabled={isGenerating || saving || (glossary === null && !error) || DEMO_MODE}
              onChange={(event) => void toggle(entry, event.target.checked)}
              className="mt-1 h-4 w-4 shrink-0 accent-action disabled:cursor-not-allowed" />
            <span className="min-w-0 break-words text-sm text-foreground">{entry}</span>
          </label>
        </li>)}
      </ul>
    </div>
  </div>;
}
