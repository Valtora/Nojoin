import { Loader2, PlugZap, RefreshCw } from "lucide-react";

import { estimateContextVramGiB } from "@/lib/aiProviders";
import type { ProviderConfigGap } from "@/lib/aiAvailability";
import { cn } from "@/lib/cn";
import SettingsStatusBadge from "./SettingsStatusBadge";
import type { ValidationMessage } from "./useAISettingsModels";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";

/** The one status vocabulary for a provider, on every AI card. */
export function ProviderStatusBadge({ gap }: { gap: ProviderConfigGap | null }) {
  if (gap === null) {
    return <SettingsStatusBadge tone="success">Ready</SettingsStatusBadge>;
  }
  return (
    <SettingsStatusBadge tone="warning">
      {gap === "model" ? "No model selected" : "Not set up"}
    </SettingsStatusBadge>
  );
}

/**
 * A model select with its own refresh button beside it. The button is a
 * sibling rather than part of the label, so each control keeps its own name.
 */
export function ModelPicker({
  id,
  value,
  options,
  placeholder,
  allowEmpty = false,
  disabled = false,
  onChange,
  onRefresh,
  refreshing = false,
  refreshDisabled = false,
  refreshLabel = "Reload the model list",
}: {
  id: string;
  value: string;
  options: Array<string | { value: string; label: string }>;
  /** The empty option's text; `null` leaves no empty option. */
  placeholder: string | null;
  /**
   * Whether the empty option can be chosen. Off, it only shows while nothing
   * is selected, so a required model cannot be cleared by accident.
   */
  allowEmpty?: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
  onRefresh?: () => void;
  refreshing?: boolean;
  refreshDisabled?: boolean;
  /** The refresh button's name, when it does more than reload the list. */
  refreshLabel?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <select
        id={id}
        aria-describedby={`${id}-help`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className={cn(SETTINGS_SELECT_CLASS, "min-w-0 flex-1")}
      >
        {placeholder !== null && (
          <option value="" disabled={!allowEmpty}>
            {placeholder}
          </option>
        )}
        {options.map((option) => {
          const { value: optionValue, label } =
            typeof option === "string" ? { value: option, label: option } : option;
          return (
            <option key={optionValue} value={optionValue}>
              {label}
            </option>
          );
        })}
      </select>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing || refreshDisabled}
          aria-label={refreshLabel}
          title={refreshLabel}
          className={cn(SETTINGS_BUTTON_SECONDARY, "h-10 w-10 shrink-0 px-0")}
        >
          <RefreshCw
            className={cn("h-4 w-4", refreshing && "animate-spin")}
            aria-hidden="true"
          />
        </button>
      )}
    </div>
  );
}

/** Context window guidance, with the VRAM the chosen value costs. */
export function ContextWindowHelp({ tokens }: { tokens: number }) {
  const gib = estimateContextVramGiB(tokens);

  return (
    <>
      The tokens Ollama holds per request (<code>num_ctx</code>). On a 14B
      model each one needs about 190 KiB of GPU memory, so{" "}
      {tokens.toLocaleString("en-GB")} tokens needs about{" "}
      <strong className="font-semibold text-foreground">
        {gib.toFixed(1)} GB
      </strong>{" "}
      on top of the model. Ollama refuses to load a window that does not fit. A
      meeting longer than the window is written in parts.
    </>
  );
}

/** Tests a provider and reports the result in a live region. */
export function TestConnection({
  id,
  provider,
  validating,
  message,
  onTest,
}: {
  id: string;
  provider: string;
  validating: string | null;
  message: ValidationMessage | null;
  onTest: () => void;
}) {
  const busy = validating === provider;
  const result = message?.provider === provider ? message : null;

  return (
    <div className="space-y-2">
      <button
        id={id}
        type="button"
        onClick={onTest}
        disabled={Boolean(validating)}
        aria-describedby={`${id}-help`}
        className={SETTINGS_BUTTON_SECONDARY}
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <PlugZap className="h-4 w-4" aria-hidden="true" />
        )}
        {busy ? "Testing..." : "Test connection"}
      </button>
      <p
        role="status"
        className={cn(
          "text-xs",
          result?.type === "success" && "text-status-success-fg",
          result?.type === "error" && "text-status-danger-fg",
        )}
      >
        {result?.msg}
      </p>
    </div>
  );
}
