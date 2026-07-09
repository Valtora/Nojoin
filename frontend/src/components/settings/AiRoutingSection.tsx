import { useRef, type ReactNode } from "react";
import { Check, Cloud, Info, Server } from "lucide-react";

import { Settings } from "@/types";
import { cn } from "@/lib/cn";
import CliOAuthPanel from "./CliOAuthPanel";
import SettingsCallout from "./SettingsCallout";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { checkLlmConfigured } from "./aiSettingsModels";
import { CLI_MODEL_OPTIONS } from "./cliModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all";

interface AiRoutingSectionProps {
  settings: Settings;
  /** Apply and save immediately (discrete controls). */
  onPersist: (newSettings: Settings) => void;
  isAdmin: boolean;
  cliConnected: boolean;
  onCliConnectedChange: (connected: boolean) => void;
}

/**
 * Per-user "AI routing" section. The user picks between the server-configured
 * provider and routing inference through their own Claude subscription (CLI
 * OAuth). Only the controls relevant to the active choice are shown. The two
 * former no-op options (Ollama/BYOK) were removed — they never changed
 * resolution (they fell through to the server provider).
 */
export default function AiRoutingSection({
  settings,
  onPersist,
  isAdmin,
  cliConnected,
  onCliConnectedChange,
}: AiRoutingSectionProps) {
  const isCli = settings.usage_model === "cli_oauth";
  const providerConfigured = checkLlmConfigured(settings);
  const activeProvider = settings.llm_provider || "none";
  const secondaryProvider = settings.secondary_llm_provider;

  // When a user without a live subscription picks "My subscription", scroll them
  // to the connect panel below instead of dead-ending on a disabled control
  // (which showed the OS "not-allowed" cursor and no path forward).
  const connectPanelRef = useRef<HTMLDivElement>(null);

  const chooseServer = () => {
    if (!isCli) return;
    onPersist({ ...settings, usage_model: null });
  };
  const chooseCli = () => {
    if (isCli) return;
    if (!cliConnected) {
      connectPanelRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
      return;
    }
    onPersist({ ...settings, usage_model: "cli_oauth" });
  };

  return (
    <SettingsSection
      eyebrow="AI"
      title="AI routing"
      description="Choose how AI runs for your account. This is a per-user preference and does not change anything for other users."
      width="wide"
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
            title="My Claude subscription"
            description={
              cliConnected || isCli
                ? "Route AI through your own Claude subscription — usually faster, and you can pick a stronger model."
                : "Not connected yet — click to set it up below. Usually faster than the shared server default."
            }
          />
        </div>

        <p className="text-xs contrast-helper">
          You choose how AI runs for your account: the server default is managed
          by your administrator with nothing for you to set up, or connect your
          own subscription to run on your personal plan — usually faster and
          often higher quality.
        </p>

        <div ref={connectPanelRef}>
          <CliOAuthPanel onConnectedChange={onCliConnectedChange} />
        </div>

        {isCli ? (
          <div className="space-y-4">
            {!cliConnected && (
              <SettingsCallout tone="warning">
                Your Claude subscription is not connected right now. Reconnect it
                above, or switch to the server default, so AI keeps working.
              </SettingsCallout>
            )}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Model for your subscription
                </label>
                <select
                  value={settings.cli_model || ""}
                  onChange={(e) =>
                    onPersist({
                      ...settings,
                      cli_model: e.target.value ? e.target.value : null,
                    })
                  }
                  className={SELECT_CLASS}
                >
                  <option value="">
                    Claude&apos;s default (currently a Sonnet)
                  </option>
                  {CLI_MODEL_OPTIONS.map((model) => (
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
                  value={settings.cli_live_model || ""}
                  onChange={(e) =>
                    onPersist({
                      ...settings,
                      cli_live_model: e.target.value ? e.target.value : null,
                    })
                  }
                  className={SELECT_CLASS}
                >
                  <option value="">Same as the model above</option>
                  {CLI_MODEL_OPTIONS.map((model) => (
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
                  falls back to the server&apos;s secondary provider
                  {secondaryProvider ? (
                    <>
                      {" "}
                      (
                      <span className="capitalize">{secondaryProvider}</span>)
                    </>
                  ) : (
                    " (if the administrator has configured one)"
                  )}
                  .
                </span>
              </span>
            </SettingsCallout>
          </div>
        ) : (
          <SettingsPanel
            variant="subtle"
            className="flex items-center justify-between gap-3"
          >
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-white">
                Server provider:{" "}
                <span className="capitalize text-orange-600 dark:text-orange-400">
                  {activeProvider}
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
          </SettingsPanel>
        )}
      </div>
    </SettingsSection>
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
          : "border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-950/60 dark:hover:border-gray-600",
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
