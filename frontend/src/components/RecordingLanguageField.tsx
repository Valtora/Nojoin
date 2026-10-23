"use client";

import { useEffect, useState } from "react";

import Select from "@/components/ui/Select";
import { getLanguageOptions, getSettings } from "@/lib/api";
import { LanguageRegistry } from "@/types";

export type TranscriptionEngine = keyof LanguageRegistry["engine_capabilities"];

/** The option that leaves the recording on the owner's setting (null). */
const DEFAULT_VALUE = "";

const toEngine = (backend: string | undefined): TranscriptionEngine =>
  backend === "canary" || backend === "parakeet" ? backend : "whisper";

interface RecordingLanguageFieldProps {
  /** "auto" or a listed code; null follows the owner's setting. */
  value: string | null;
  onChange: (value: string | null) => void;
  /** The engine that will run. Omitted, the field reads the owner's from Settings. */
  backend?: TranscriptionEngine;
  disabled?: boolean;
  idPrefix?: string;
}

/**
 * The language one recording is transcribed in, for the import and reprocess
 * dialogs. Hidden on an engine that can't take a language (Parakeet). On one
 * that can't detect (Canary) Auto-detect is left out and a stored "auto" shows
 * as English, which is what that engine transcribes it as.
 */
export default function RecordingLanguageField({
  value,
  onChange,
  backend,
  disabled = false,
  idPrefix = "recording-language",
}: RecordingLanguageFieldProps) {
  const [registry, setRegistry] = useState<LanguageRegistry | null>(null);
  const [ownerBackend, setOwnerBackend] = useState<TranscriptionEngine | null>(
    null,
  );

  useEffect(() => {
    let active = true;
    getLanguageOptions()
      .then((loaded) => active && setRegistry(loaded))
      .catch(console.error);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (backend) return;
    let active = true;
    getSettings()
      .then(
        (settings) =>
          active && setOwnerBackend(toEngine(settings.transcription_backend)),
      )
      .catch(console.error);
    return () => {
      active = false;
    };
  }, [backend]);

  const engine = backend ?? ownerBackend;
  const capability = engine ? registry?.engine_capabilities[engine] : undefined;
  if (!registry || !capability || !capability.forced_language) return null;

  const canAutoDetect = capability.auto_detect !== false;
  const languages = registry.transcription_languages.filter(
    (language) => canAutoDetect || language.code !== "auto",
  );
  const shown =
    value === null ? DEFAULT_VALUE : !canAutoDetect && value === "auto" ? "en" : value;

  return (
    <Select
      id={`${idPrefix}-select`}
      label="Language"
      hint={
        canAutoDetect
          ? "Your default is the transcription language in Settings."
          : capability.guidance
      }
      value={shown}
      onChange={(event) =>
        onChange(event.target.value === DEFAULT_VALUE ? null : event.target.value)
      }
      disabled={disabled}
    >
      <option value={DEFAULT_VALUE}>Your default</option>
      {languages.map((language) => (
        <option key={language.code} value={language.code}>
          {language.label}
        </option>
      ))}
    </Select>
  );
}
