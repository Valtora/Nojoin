import { ButtonBase } from "@/components/ui/nativeBases";
import Link from "next/link";
import {
  useEffect,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Check, Cloud, Server } from "lucide-react";

import { CliOAuthStatus, CliProvider, Settings } from "@/types";
import { cn } from "@/lib/cn";
import CliOAuthPanel from "./CliOAuthPanel";
import SettingsCallout from "./SettingsCallout";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { settingsCategoryHref } from "./settingsCategories";
import { ModelPicker, ProviderStatusBadge } from "./aiProviderControls";
import { SETTINGS_VALUE_CLASS, SETTINGS_CONTROL_COLUMN_WIDE } from "./settingsControls";
import { aiProviderLabel } from "@/lib/aiProviders";
import {
  getClaudeCliModels,
  getCodexModels,
  refreshCodexModels,
} from "@/lib/api/cliOauth";
import { getServerProviderGap } from "@/lib/aiAvailability";
import { cliModelOptions, type CliModelOption } from "./cliModels";


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
 * shown.
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
  const serverGap = getServerProviderGap(settings);
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

  const chooseServer = () => {
    if (!isCli) return;
    onPersist({ ...settings, usage_model: null });
  };
  const chooseCli = () => {
    if (isCli) return;
    if (!anyConnected) {
      // Nothing connected yet — guide the user to the connect panel below
      // instead of dead-ending on a disabled control.
      document.getElementById("your-ai-subscription")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
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

  const serverLabel = aiProviderLabel(settings.llm_provider || "gemini");
  const fallbackLabel = secondaryProvider ? aiProviderLabel(secondaryProvider) : null;

  return (
    <>
      <SettingsCard
        id="your-ai-routing"
        title="How AI runs for you"
        description="Only your own meetings follow this choice."
      >
        <SettingsBlock>
          <RoutingChoice
            isCli={isCli}
            onChooseServer={chooseServer}
            onChooseCli={chooseCli}
            serverDescription={
              isAdmin
                ? "Runs on the default provider set under AI providers."
                : "Runs on the provider your administrator set up."
            }
            cliDescription={
              anyConnected || isCli
                ? "Runs on your own Claude or ChatGPT plan, usually faster, with a model you choose."
                : "Connect a subscription below to use this."
            }
          />
        </SettingsBlock>

        {isCli ? (
          <>
            {!activeProviderConnected && (
              <SettingsBlock>
                <SettingsCallout tone="warning">
                  Your {PROVIDER_LABEL[activeProvider]} subscription is not
                  connected. Reconnect it below, or switch to the server
                  default, so AI keeps working.
                </SettingsCallout>
              </SettingsBlock>
            )}

            <SettingsRow
              id="your-ai-model"
              label="Model"
              controlId="your-ai-model-select"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
              description="Used for your notes, titles, speaker names and Meeting Chat."
            >
              <ModelPicker
                id="your-ai-model-select"
                value={mainModel}
                options={modelOptions.map((model) => ({
                  value: model.id,
                  label: model.label,
                }))}
                placeholder={DEFAULT_MODEL_HINT[activeProvider]}
                allowEmpty
                onChange={(value) => setModel("main", value)}
                onRefresh={() => void handleRefreshModels()}
                refreshing={refreshingModels}
                refreshLabel={
                  activeProvider === "codex"
                    ? "Ask Codex for its current models"
                    : "Reload the model list"
                }
              />
            </SettingsRow>

            <SettingsRow
              id="your-ai-live-model"
              label="Meeting Edge model"
              controlId="your-ai-live-model-select"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
              description="Used for live guidance. A faster model keeps it responsive and uses less of your quota."
            >
              <ModelPicker
                id="your-ai-live-model-select"
                value={liveModel}
                options={modelOptions.map((model) => ({
                  value: model.id,
                  label: model.label,
                }))}
                placeholder="Same as the model above"
                allowEmpty
                onChange={(value) => setModel("live", value)}
              />
            </SettingsRow>

            <SettingsBlock>
              <SettingsCallout tone="info">
                When your subscription is unavailable or rate-limited, the
                server&apos;s default provider ({serverLabel}) answers
                {fallbackLabel
                  ? `, then its fallback (${fallbackLabel}) if that fails too.`
                  : ". A fallback answers after that if your administrator has set one up."}
              </SettingsCallout>
            </SettingsBlock>
          </>
        ) : (
          <SettingsRow
            label="Server provider"
            badge={<ProviderStatusBadge gap={serverGap} />}
            description={
              isAdmin ? (
                <>
                  Set for everyone on this installation.{" "}
                  <Link
                    href={settingsCategoryHref("ai-providers")}
                    className="font-medium text-action-text underline underline-offset-2 hover:text-action-text-hover"
                  >
                    Change it under AI providers
                  </Link>
                  .
                </>
              ) : serverGap === "model" ? (
                "Your administrator has not chosen a model yet, so AI features do not work until they do."
              ) : serverGap === "credential" ? (
                "Your administrator has not set up an AI provider yet."
              ) : (
                "Set by your administrator for everyone who uses the server default."
              )
            }
          >
            <p className={SETTINGS_VALUE_CLASS}>{serverLabel}</p>
          </SettingsRow>
        )}
      </SettingsCard>

      <CliOAuthPanel onStatusChange={setCliStatus} />
    </>
  );
}

const CHOICES = ["server", "cli"] as const;

/**
 * The two ways AI can run, as a radio group: one tab stop, arrow keys move
 * between the options, as the ARIA radio pattern expects.
 */
function RoutingChoice({
  isCli,
  onChooseServer,
  onChooseCli,
  serverDescription,
  cliDescription,
}: {
  isCli: boolean;
  onChooseServer: () => void;
  onChooseCli: () => void;
  serverDescription: string;
  cliDescription: string;
}) {
  const selected = isCli ? "cli" : "server";
  const choose = (choice: (typeof CHOICES)[number]) =>
    choice === "cli" ? onChooseCli() : onChooseServer();

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const next = CHOICES[(CHOICES.indexOf(selected) + step + CHOICES.length) % CHOICES.length];
    choose(next);
    event.currentTarget
      .querySelector<HTMLButtonElement>(`[data-choice="${next}"]`)
      ?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label="How AI runs for you"
      onKeyDown={handleKeyDown}
      className="grid gap-3 @min-[36rem]:grid-cols-2"
    >
      <RoutingOption
        choice="server"
        selected={selected === "server"}
        onSelect={() => choose("server")}
        icon={<Server className="h-4 w-4" aria-hidden="true" />}
        title="Server default"
        description={serverDescription}
      />
      <RoutingOption
        choice="cli"
        selected={selected === "cli"}
        onSelect={() => choose("cli")}
        icon={<Cloud className="h-4 w-4" aria-hidden="true" />}
        title="My own subscription"
        description={cliDescription}
      />
    </div>
  );
}

function RoutingOption({
  choice,
  selected,
  onSelect,
  icon,
  title,
  description,
}: {
  choice: (typeof CHOICES)[number];
  selected: boolean;
  onSelect: () => void;
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <ButtonBase
      type="button"
      role="radio"
      aria-checked={selected}
      tabIndex={selected ? 0 : -1}
      data-choice={choice}
      onClick={onSelect}
      className={cn(
        "flex flex-col gap-2 rounded-lg border p-4 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
        selected
          ? "border-action bg-action-tint"
          : "border-control-border bg-control-bg hover:bg-surface-inset",
      )}
    >
      <span className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className={selected ? "text-action-text" : "contrast-icon-muted"}>
            {icon}
          </span>
          {title}
        </span>
        <span
          aria-hidden="true"
          className={cn(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
            selected
              ? "border-action bg-action text-action-on"
              : "border-control-border",
          )}
        >
          {selected && <Check className="h-3 w-3" />}
        </span>
      </span>
      <span className="text-xs leading-5 contrast-helper">{description}</span>
    </ButtonBase>
  );
}
