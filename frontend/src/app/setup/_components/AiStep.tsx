import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle,
  Clock,
  Cloud,
  Loader2,
  RefreshCw,
  Server,
} from "lucide-react";
import type { ReactNode } from "react";

import Button from "@/components/ui/Button";
import Select from "@/components/ui/Select";
import CliOAuthPanel from "@/components/settings/CliOAuthPanel";
import { cn } from "@/lib/cn";
import type { CliOAuthStatus, CliProvider } from "@/types";

import type { AiRoute } from "../_hooks/useSetupWizard";

const ENV_DOC_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#configure-env";

const PROVIDER_LABEL: Record<string, string> = {
  gemini: "Google Gemini",
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
};

const CLI_PROVIDER_LABEL: Record<CliProvider, string> = {
  claude_code: "Claude",
  codex: "ChatGPT",
};

const MODEL_RECOMMENDATIONS: Record<string, string[]> = {
  gemini: [
    "gemini-flash-latest: faster responses, good for straightforward meetings.",
    "gemini-pro-latest: better reasoning, worth it for dense or technical meetings.",
  ],
  openai: [
    "A mini-tier GPT-5 model: faster and cheaper for routine notes and chat.",
    "A full GPT-5 model: stronger analysis for complex meetings.",
  ],
  anthropic: [
    "Claude Haiku: fast and cheap for routine notes and chat.",
    "Claude Sonnet: the balanced default for most meetings.",
    "Claude Opus: strongest reasoning for dense or high-stakes meetings.",
  ],
};

interface AiStepProps {
  aiRoute: AiRoute;
  onRouteChange: (route: AiRoute) => void;
  serverProvider: string;
  serverProviderSelected: boolean;
  serverCredentialPresent: boolean;
  secondaryProvider: string | null;
  secondaryProviderConfigured: boolean;
  validatingLLM: boolean;
  llmValidationMsg: { valid: boolean; msg: string } | null;
  availableModels: string[];
  selectedModel: string;
  reloadingConfig: boolean;
  savingAi: boolean;
  connectedCliProvider: CliProvider | undefined;
  onCliStatusChange: (status: CliOAuthStatus | null) => void;
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onReloadConfig: () => void;
  onSubmit: () => void;
}

/**
 * The AI step, which runs authenticated because the account already exists.
 *
 * Three routes, not one. Nojoin supports a server-side provider key, a per-user
 * Claude or ChatGPT subscription, and running with no AI provider at all; the
 * wizard previously modelled only the first and told everyone else to edit .env.
 * A seat-only operator with no API keys is a supported configuration, so it is
 * presented as a choice rather than as a failure.
 */
export default function AiStep({
  aiRoute,
  onRouteChange,
  serverProvider,
  serverProviderSelected,
  serverCredentialPresent,
  secondaryProvider,
  secondaryProviderConfigured,
  validatingLLM,
  llmValidationMsg,
  availableModels,
  selectedModel,
  reloadingConfig,
  savingAi,
  connectedCliProvider,
  onCliStatusChange,
  onInputChange,
  onReloadConfig,
  onSubmit,
}: AiStepProps) {
  const providerLabel = PROVIDER_LABEL[serverProvider] || serverProvider;
  const serverReady = Boolean(llmValidationMsg?.valid && selectedModel);
  const canContinue = aiRoute !== "server" || serverReady;
  const recommendations = MODEL_RECOMMENDATIONS[serverProvider];

  return (
    <div className="space-y-5">
      <div className="text-center mb-2">
        <h2 className="text-xl font-semibold text-foreground">AI Configuration</h2>
        <p className="text-sm text-contrast-helper mt-2">
          Choose how Nojoin runs AI for meeting notes, chat, speaker inference,
          and Meeting Edge
        </p>
      </div>

      <div role="radiogroup" aria-label="AI route" className="space-y-2">
        <RouteCard
          selected={aiRoute === "server"}
          onSelect={() => onRouteChange("server")}
          icon={<Server className="h-4 w-4" />}
          title="This server's AI provider"
          description={
            serverCredentialPresent
              ? `Uses the ${providerLabel} credential found in the server's .env, shared by every account.`
              : "No provider credential was found in the server's .env."
          }
          badge={
            serverCredentialPresent
              ? validatingLLM
                ? "Checking"
                : llmValidationMsg?.valid
                  ? "Ready"
                  : "Failed"
              : "Not configured"
          }
          badgeTone={
            serverCredentialPresent && llmValidationMsg?.valid
              ? "success"
              : serverCredentialPresent && llmValidationMsg
                ? "danger"
                : "muted"
          }
        />
        <RouteCard
          selected={aiRoute === "subscription"}
          onSelect={() => onRouteChange("subscription")}
          icon={<Cloud className="h-4 w-4" />}
          title="My own Claude or ChatGPT subscription"
          description="Route AI through your own Claude Pro/Max or ChatGPT Plus/Pro plan. No API key needed."
          badge={connectedCliProvider ? "Connected" : undefined}
          badgeTone="success"
        />
        <RouteCard
          selected={aiRoute === "later"}
          onSelect={() => onRouteChange("later")}
          icon={<Clock className="h-4 w-4" />}
          title="Decide later"
          description="Record, transcribe and separate speakers now; set AI up whenever you like."
        />
      </div>

      {aiRoute === "server" && (
        <div className="space-y-4">
          {validatingLLM ? (
            <div className="flex flex-col items-center justify-center py-8 space-y-3">
              <Loader2 className="w-8 h-8 animate-spin text-action-text" />
              <p className="text-sm text-contrast-helper">
                Connecting to {providerLabel} and loading models...
              </p>
            </div>
          ) : !serverCredentialPresent ? (
            <>
              <div className="p-4 bg-surface-inset/40 border border-surface-border rounded-xl space-y-2 text-contrast-helper">
                <p className="text-sm font-semibold text-foreground">
                  {serverProviderSelected
                    ? `No ${providerLabel} credential in the server environment`
                    : "No AI provider is configured on this server"}
                </p>
                <p className="text-xs leading-relaxed">
                  {serverProviderSelected
                    ? `The server's .env selects ${providerLabel} but carries no credential for it.`
                    : "A normal way to run Nojoin. Pick one of the other routes above, or set LLM_PROVIDER and its key in the server's .env."}
                </p>
                <p className="text-xs leading-relaxed">
                  Set{" "}
                  <code className="bg-surface-inset px-1 rounded">LLM_PROVIDER</code>{" "}
                  and its key (Ollama needs only{" "}
                  <code className="bg-surface-inset px-1 rounded">OLLAMA_API_URL</code>
                  ), restart the stack, then check again. Keep this tab open: your
                  progress is kept.{" "}
                  <a
                    href={ENV_DOC_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-action-text hover:text-action-text-hover"
                  >
                    Configuration reference
                  </a>
                </p>
              </div>
              <Button
                variant="secondary"
                fullWidth
                loading={reloadingConfig}
                onClick={onReloadConfig}
                iconLeft={<RefreshCw className="w-4 h-4" />}
              >
                Check config again
              </Button>
            </>
          ) : (
            <>
              {llmValidationMsg && !llmValidationMsg.valid && (
                <div className="p-4 bg-status-danger-bg border border-status-danger-border rounded-xl flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-status-danger-fg shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-status-danger-fg">
                      Could not reach {providerLabel}
                    </p>
                    <p className="text-xs text-status-danger-fg mt-1">
                      {llmValidationMsg.msg}
                    </p>
                    <p className="text-xs text-contrast-helper mt-2">
                      Check the credential in the server&apos;s{" "}
                      <code className="bg-surface-inset px-1 py-0.5 rounded">.env</code>{" "}
                      and restart the stack, or pick another route above.
                    </p>
                  </div>
                </div>
              )}

              {availableModels.length > 0 && (
                <>
                  <Select
                    id="setup-llm-model"
                    name="selected_model"
                    data-field-key="selected_model"
                    label="Model"
                    value={selectedModel}
                    onChange={onInputChange}
                    hint="Used for meeting notes, chat, titles, and speaker inference. Changeable in Settings > AI providers."
                  >
                    {availableModels.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </Select>

                  {recommendations && (
                    <div className="p-3 bg-status-info-bg rounded-lg border border-status-info-border text-xs text-status-info-fg">
                      <p className="font-semibold mb-1">Recommended:</p>
                      <ul className="list-disc list-inside space-y-0.5">
                        {recommendations.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}

              {secondaryProviderConfigured && secondaryProvider && (
                <p className="text-xs text-contrast-helper">
                  A secondary provider ({PROVIDER_LABEL[secondaryProvider] ||
                    secondaryProvider}
                  ) is also configured, and answers when the primary fails.
                </p>
              )}

              <Button
                variant="ghost"
                fullWidth
                loading={reloadingConfig}
                onClick={onReloadConfig}
                iconLeft={<RefreshCw className="w-4 h-4" />}
              >
                Check config again
              </Button>
            </>
          )}
        </div>
      )}

      {aiRoute === "subscription" && (
        <div className="space-y-3">
          {/* The connect panel carries its own explanation, so this step adds
              only what the panel cannot know: that continuing commits the
              choice. */}
          <CliOAuthPanel onStatusChange={onCliStatusChange} />

          {connectedCliProvider ? (
            <p className="flex items-start gap-2 text-xs text-status-success-fg">
              <CheckCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>
                {`${CLI_PROVIDER_LABEL[connectedCliProvider]} connected. Continuing routes your account's AI through it.`}
              </span>
            </p>
          ) : (
            <p className="text-xs text-contrast-helper">
              You can continue without connecting and do this later in Settings
              &gt; Your AI.
            </p>
          )}
        </div>
      )}

      {aiRoute === "later" && (
        <div className="p-4 bg-surface-inset/40 border border-surface-border rounded-xl text-xs text-contrast-helper space-y-2">
          <p>
            <strong className="text-foreground">Works now:</strong> recording,
            transcription, speaker separation, search, tasks, people, calendar,
            and the sample meeting.
          </p>
          <p>
            <strong className="text-foreground">Waits for AI:</strong> generated
            notes and titles, meeting chat, speaker inference, Meeting Edge. You
            can run these on older meetings once AI is set up in Settings &gt;
            Your AI.
          </p>
        </div>
      )}

      <Button
        variant="primary"
        fullWidth
        loading={savingAi}
        disabled={!canContinue}
        onClick={onSubmit}
        iconRight={<ArrowRight className="w-4 h-4" />}
      >
        {aiRoute === "server" && !serverReady && !validatingLLM
          ? "Choose a working route to continue"
          : "Finish setup"}
      </Button>
    </div>
  );
}

function RouteCard({
  selected,
  onSelect,
  icon,
  title,
  description,
  badge,
  badgeTone = "muted",
}: {
  selected: boolean;
  onSelect: () => void;
  icon: ReactNode;
  title: string;
  description: string;
  badge?: string;
  badgeTone?: "success" | "danger" | "muted";
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "flex w-full flex-col gap-1.5 rounded-xl border p-3 text-left transition-colors",
        selected
          ? "border-action bg-action-tint"
          : "border-control-border hover:bg-surface-inset",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className={selected ? "text-action-text" : "text-contrast-icon-muted"}>
            {icon}
          </span>
          {title}
        </span>
        <span className="flex items-center gap-2">
          {badge && (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                badgeTone === "success" &&
                  "bg-status-success-bg text-status-success-fg border-status-success-border",
                badgeTone === "danger" &&
                  "bg-status-danger-bg text-status-danger-fg border-status-danger-border",
                badgeTone === "muted" &&
                  "bg-surface-inset text-contrast-helper border-surface-border",
              )}
            >
              {badge}
            </span>
          )}
          <span
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
      </span>
      <span className="text-xs leading-5 text-contrast-helper">{description}</span>
    </button>
  );
}
