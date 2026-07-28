import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Cloud, Info, RefreshCw, Server } from "lucide-react";

import { CliOAuthStatus, CliProvider, Settings } from "@/types";
import { cn } from "@/lib/cn";
import CliOAuthPanel from "./CliOAuthPanel";
import SettingsCallout from "./SettingsCallout";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  getClaudeCliModels,
  getCodexModels,
  refreshCodexModels,
} from "@/lib/api/cliOauth";
import { isServerProviderConfigured } from "@/lib/aiAvailability";
import { cliModelOptions, type CliModelOption } from "./cliModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50";

const PROVIDER_LABEL: Record<CliProvider, string> = {
  claude_code: "Claude",
  codex: "ChatGPT",
};

const DEFAULT_MODEL_HINT: Record<CliProvider, string> = {
  claude_code: "Claude's default (a Sonnet)",
  codex: "Codex's default",
};

// Per-provider settings fields the model pickers read/write.
const MODEL_FIELDS: Record<
  CliProvider,
  { main: keyof Settings; live: keyof Settings }
> = {
  claude_code: { main: "cli_model", live: "cli_live_model" },
  codex: { main: "codex_model", live: "codex_live_model" },
};

interface AiRoutingSectionProps {
  settings: Settings;
  /** Apply and save immediately (discrete controls). */
  onPersist: (newSettings: Settings) => void;
  isAdmin: boolean;
}

/**
 * Per-user "AI routing" section. The user picks between the server-configured
 * provider and routing inference through their own subscription (Claude or
 * ChatGPT, via CLI OAuth). Only the controls relevant to the active choice are
 * shown. The two former no-op options (Ollama/BYOK) were removed — they never
 * changed resolution (they fell through to the server provider).
 */
export default function AiRoutingSection({
  settings,
  onPersist,
  isAdmin,
}: AiRoutingSectionProps) {
  // The connect panel owns the status fetch and reports it up here, so the
  // routing controls can gate on (and select between) live subscriptions.
  const [cliStatus, setCliStatus] = useState<CliOAuthStatus | null>(null);
  // Live Codex model catalogue (from `codex debug models`); null until loaded.
  const [codexModels, setCodexModels] = useState<CliModelOption[] | null>(null);
  const [claudeModels, setClaudeModels] = useState<CliModelOption[] | null>(null);
  const [refreshingModels, setRefreshingModels] = useState(false);
  // Bumped by the refresh button to re-run the catalogue effect.
  const [modelReload, setModelReload] = useState(0);

  const isCli = settings.usage_model === "cli_oauth";
  const providerConfigured = isServerProviderConfigured(settings);
  const activeServerProvider = settings.llm_provider || "none";
  const secondaryProvider = settings.secondary_llm_provider;

  // Only one subscription can be connected at a time; the active provider is
  // simply whichever one is connected.
  const connectedProvider: CliProvider | undefined = (cliStatus?.providers ?? [])
    .find((entry) => entry.connected)
    ?.provider;
  const anyConnected = connectedProvider !== undefined;
  const activeProvider: CliProvider =
    connectedProvider || settings.cli_provider || "claude_code";
  const activeProviderConnected = connectedProvider === activeProvider;

  // Keep cli_provider in sync with the connected subscription, so routing
  // resolves to the one the user actually connected (not a stale choice).
  useEffect(() => {
    if (isCli && connectedProvider && connectedProvider !== settings.cli_provider) {
      onPersist({ ...settings, cli_provider: connectedProvider });
    }
  }, [isCli, connectedProvider, settings, onPersist]);

  // Scroll target for the "not connected yet" call-to-action (see chooseCli).
  const connectPanelRef = useRef<HTMLDivElement>(null);

  const chooseServer = () => {
    if (!isCli) return;
    onPersist({ ...settings, usage_model: null });
  };
  const chooseCli = () => {
    if (isCli) return;
    if (!anyConnected) {
      // Nothing connected yet — guide the user to the connect panel below
      // instead of dead-ending on a disabled control.
      connectPanelRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      return;
    }
    onPersist({
      ...settings,
      usage_model: "cli_oauth",
      cli_provider: activeProvider,
    });
  };

  const setModel = (kind: "main" | "live", value: string) => {
    const field = MODEL_FIELDS[activeProvider][kind];
    onPersist({ ...settings, [field]: value ? value : null });
  };

  const mainModel =
    (settings[MODEL_FIELDS[activeProvider].main] as string | null | undefined) ||
    "";
  const liveModel =
    (settings[MODEL_FIELDS[activeProvider].live] as string | null | undefined) ||
    "";
  // Both catalogues come from the API. Codex's is fetched live from the binary
  // (it changes per codex version); Claude's is curated server-side, because a
  // subscription exposes no models endpoint. cliModelOptions is only the
  // offline fallback until either loads.
  useEffect(() => {
    if (!isCli) return;
    let cancelled = false;
    let refetch: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        if (activeProvider === "codex") {
          const result = await getCodexModels();
          if (cancelled) return;
          if (result.models?.length) setCodexModels(result.models);
          // A fallback response means the worker is still fetching the live
          // catalogue — refetch shortly for the real list.
          if (result.source === "fallback") {
            refetch = setTimeout(load, 3000);
          } else {
            setRefreshingModels(false);
          }
        } else {
          const result = await getClaudeCliModels();
          if (cancelled) return;
          if (result.models?.length) setClaudeModels(result.models);
          setRefreshingModels(false);
        }
      } catch {
        // Keep whichever list is already showing.
        if (!cancelled) setRefreshingModels(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(refetch);
    };
  }, [isCli, activeProvider, modelReload]);

  const handleRefreshModels = async () => {
    setRefreshingModels(true);
    try {
      // Only Codex has a cache to bust; Claude's list is curated, so the button
      // simply re-reads it rather than being disabled and looking broken.
      if (activeProvider === "codex") await refreshCodexModels();
    } catch (error) {
      console.error("Failed to refresh the model catalogue", error);
    } finally {
      setModelReload((value) => value + 1);
    }
  };

  const liveModelOptions = activeProvider === "codex" ? codexModels : claudeModels;
  const modelOptions = liveModelOptions ?? cliModelOptions(activeProvider);

  return (
    <SettingsCard
      title="AI routing"
      description="Choose how AI runs for your account. This is a per-user preference and does not change anything for other users."
    >
      <div className="mx-auto max-w-3xl space-y-4">
        <div
          role="radiogroup"
          aria-label="AI routing"
          className="grid gap-3 sm:grid-cols-2"
        >
          <RoutingCard
            selected={!isCli}
            onSelect={chooseServer}
            icon={<Server className="h-4 w-4" />}
            title="Use server default"
            description="AI runs on the provider your server administrator configured."
          />
          <RoutingCard
            selected={isCli}
            onSelect={chooseCli}
            icon={<Cloud className="h-4 w-4" />}
            title="My own AI subscription"
            description={
              anyConnected || isCli
                ? "Route AI through your own Claude or ChatGPT plan — usually faster, and you can pick a stronger model."
                : "Not connected yet — click to set it up below. Usually faster than the shared server default."
            }
          />
        </div>

        <p className="text-xs contrast-helper">
          You choose how AI runs for your account: the server default is managed
          by your administrator with nothing for you to set up, or connect your
          own Claude or ChatGPT subscription to run on your personal plan —
          usually faster and often higher quality.
        </p>

        <div ref={connectPanelRef}>
          <CliOAuthPanel onStatusChange={setCliStatus} />
        </div>

        {isCli ? (
          <div className="space-y-4">
            {!activeProviderConnected && (
              <SettingsCallout tone="warning">
                Your {PROVIDER_LABEL[activeProvider]} subscription is not
                connected right now. Reconnect it above, or use the server
                default, so AI keeps working.
              </SettingsCallout>
            )}

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Model for your subscription
                  </label>
                  <button
                    type="button"
                    onClick={handleRefreshModels}
                    disabled={refreshingModels}
                    title={
                      activeProvider === "codex"
                        ? "Re-query your Codex CLI for its current models"
                        : "Reload the model list"
                    }
                    className="inline-flex items-center gap-1.5 text-xs contrast-helper hover:text-gray-900 dark:hover:text-white disabled:opacity-60"
                  >
                    <RefreshCw
                      className={`h-3.5 w-3.5 ${refreshingModels ? "animate-spin" : ""}`}
                    />
                    {refreshingModels ? "Refreshing..." : "Refresh models"}
                  </button>
                </div>
                <select
                  value={mainModel}
                  onChange={(event) => setModel("main", event.target.value)}
                  className={SELECT_CLASS}
                >
                  <option value="">{DEFAULT_MODEL_HINT[activeProvider]}</option>
                  {modelOptions.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs contrast-helper">
                  Used for notes, titles, speaker inference, and chat.
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Meeting Edge model for your subscription
                </label>
                <select
                  value={liveModel}
                  onChange={(event) => setModel("live", event.target.value)}
                  className={SELECT_CLASS}
                >
                  <option value="">Same as the model above</option>
                  {modelOptions.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs contrast-helper">
                  Used for live Meeting Edge. A faster model keeps guidance
                  responsive and conserves your subscription quota.
                </p>
              </div>
            </div>
            <SettingsCallout tone="info">
              <span className="flex items-start gap-2">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  When your subscription is unavailable or usage-limited, Nojoin
                  falls back to the server&apos;s default provider chain: its
                  primary provider
                  {settings.llm_provider ? (
                    <>
                      {" "}
                      (
                      <span className="capitalize">
                        {settings.llm_provider}
                      </span>
                      )
                    </>
                  ) : null}
                  {secondaryProvider ? (
                    <>
                      {", then its secondary provider ("}
                      <span className="capitalize">{secondaryProvider}</span>
                      {") if the primary also fails"}
                    </>
                  ) : (
                    " (a secondary is used after that if the administrator has configured one)"
                  )}
                  .
                </span>
              </span>
            </SettingsCallout>
          </div>
        ) : (
          <SettingsBlock inset
            className="flex items-center justify-between gap-3"
          >
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-white">
                Server provider:{" "}
                <span className="capitalize text-orange-600 dark:text-orange-400">
                  {activeServerProvider}
                </span>
              </div>
              <p className="mt-1 text-xs contrast-helper">
                {isAdmin
                  ? "Configured on this server. Change the provider and model in the Server provider section below."
                  : "Configured by your server administrator. Every account on the server default shares this provider."}
              </p>
            </div>
            <SettingsStatusBadge
              tone={providerConfigured ? "success" : "warning"}
            >
              {providerConfigured ? "Configured" : "Not configured"}
            </SettingsStatusBadge>
          </SettingsBlock>
        )}
      </div>
    </SettingsCard>
  );
}

function RoutingCard({
  selected,
  onSelect,
  icon,
  title,
  description,
}: {
  selected: boolean;
  onSelect: () => void;
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "flex flex-col gap-2 rounded-2xl border p-4 text-left transition-all",
        selected
          ? "border-orange-500 bg-orange-50/60 shadow-sm dark:border-orange-500/50 dark:bg-orange-500/10"
          : "settings-inset border-transparent hover:border-gray-300 dark:hover:border-gray-600",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
          <span className={selected ? "text-orange-500" : "contrast-icon-muted"}>
            {icon}
          </span>
          {title}
        </span>
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full border",
            selected
              ? "border-orange-500 bg-orange-500 text-white"
              : "border-gray-300 dark:border-gray-600",
          )}
        >
          {selected && <Check className="h-3 w-3" />}
        </span>
      </div>
      <p className="text-xs leading-5 contrast-helper">{description}</p>
    </button>
  );
}
