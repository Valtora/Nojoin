"use client";

import { useEffect, useRef } from "react";
import Image from "next/image";
import { Loader2, AlertTriangle } from "lucide-react";

import { WHISPER_MODELS } from "@/lib/whisperModels";

import AccountStep from "./_components/AccountStep";
import AiStep from "./_components/AiStep";
import CompleteStep from "./_components/CompleteStep";
import LegalStep from "./_components/LegalStep";
import TranscriptionStep from "./_components/TranscriptionStep";
import UnlockStep from "./_components/UnlockStep";
import {
  SETUP_STEPS,
  STEP_ACCOUNT,
  STEP_AI,
  STEP_COMPLETE,
  STEP_LEGAL,
  STEP_TRANSCRIPTION,
  STEP_UNLOCK,
  useSetupWizard,
} from "./_hooks/useSetupWizard";

const CLI_PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude subscription",
  codex: "ChatGPT subscription",
};

const PROVIDER_LABEL: Record<string, string> = {
  gemini: "Google Gemini",
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
};

export default function SetupPage() {
  const wizard = useSetupWizard();
  // Swapping the step swaps the whole panel without moving focus, which leaves
  // a screen reader (and a keyboard user mid-tab) stranded on the old context.
  // Focusing the panel puts both at the top of what actually changed.
  const stepPanelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    stepPanelRef.current?.focus();
  }, [wizard.step]);
  const {
    loading,
    step,
    formData,
    error,
    unlocking,
    reloadingConfig,
    creatingAccount,
    savingAi,
    includeDemoRecording,
    enableTelemetry,
    setEnableTelemetry,
    setIncludeDemoRecording,
    ffmpegMissing,
    aiRoute,
    setAiRoute,
    validatingLLM,
    llmValidationMsg,
    availableModels,
    serverProvider,
    serverProviderSelected,
    serverCredentialPresent,
    secondaryProvider,
    secondaryProviderConfigured,
    setCliStatus,
    connectedCliProvider,
    pyannoteModelsReady,
    bundledPyannoteModelsReady,
    hfTokenPresent,
    modelPreparationProgress,
    modelPreparationMessage,
    modelPreparationStage,
    modelPreparationComplete,
    modelPreparationWarning,
    goToStep,
    handleInputChange,
    handleBootstrapPasswordChange,
    handleUnlockSubmit,
    handleLegalSubmit,
    handleTranscriptionSubmit,
    handleAccountSubmit,
    handleReloadConfig,
    handleAiSubmit,
    handleCompleteSetup,
  } = wizard;

  if (loading) {
    return (
      <div className="min-h-dvh flex items-center justify-center bg-surface-page">
        <Loader2 className="w-8 h-8 animate-spin text-action-text" />
      </div>
    );
  }

  const currentStepIndex = SETUP_STEPS.findIndex((entry) => entry.id === step);
  const whisperModelLabel =
    WHISPER_MODELS.find((model) => model.id === formData.whisper_model_size)
      ?.label || formData.whisper_model_size;

  const aiDetail =
    aiRoute === "subscription" && connectedCliProvider
      ? CLI_PROVIDER_LABEL[connectedCliProvider]
      : aiRoute === "server" && formData.selected_model
        ? `${PROVIDER_LABEL[serverProvider] || serverProvider} (${formData.selected_model})`
        : "Not configured yet";

  return (
    // page-shell: this route owns its scroll, because <body> is overflow-hidden
    // for the dashboard's sake. The wizard is the tallest of these pages and it
    // clipped -- 660px of content in a 420px viewport, unreachable.
    <div className="page-shell flex flex-col items-center bg-surface-page p-4">
      {/* shrink-0 is load-bearing. `overflow-hidden` (which the rounded header
          needs) zeroes this flex item's automatic minimum size, so without it
          the card shrinks to the viewport and clips its own content rather than
          overflowing into page-shell's scroll: nothing scrolled, and only moving
          focus revealed the rest. /oauth/authorize avoids the same trap by
          putting my-auto on a plain wrapper around its card instead. */}
      <div className="my-auto w-full max-w-xl shrink-0 bg-surface-card rounded-surface border border-surface-border shadow-card overflow-hidden">
        {/* The full welcome earns its space once, on the gate. Past it, every
            step needs the vertical room for its own content instead. */}
        {step === STEP_UNLOCK ? (
          <div className="bg-action p-6 text-center">
            <div className="flex justify-center mb-4">
              <div className="bg-action-on p-3 rounded-full">
                <Image
                  src="/assets/NojoinLogo.png"
                  alt="Nojoin"
                  width={48}
                  height={48}
                  className="w-12 h-12"
                />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-action-on">
              Welcome to Nojoin
            </h1>
            <p className="text-action-on-muted mt-2">Initial System Setup</p>
          </div>
        ) : (
          <div className="bg-action px-5 py-3 flex items-center gap-3">
            <div className="bg-action-on p-1.5 rounded-full shrink-0">
              <Image
                src="/assets/NojoinLogo.png"
                alt="Nojoin"
                width={24}
                height={24}
                className="w-6 h-6"
              />
            </div>
            <h1 className="text-base font-semibold text-action-on">
              Nojoin setup
            </h1>
          </div>
        )}

        {/* Progress (hidden on the unlock gate, which is not part of setup
            proper). Named rather than five anonymous stripes, so the operator
            can see what is left. */}
        {step > STEP_UNLOCK && currentStepIndex >= 0 && (
          <div className="border-b border-surface-divider px-4 pt-3 pb-2">
            <div className="flex items-center justify-between text-[11px] text-contrast-helper">
              <span>
                Step {currentStepIndex + 1} of {SETUP_STEPS.length}:{" "}
                {SETUP_STEPS[currentStepIndex].label}
              </span>
            </div>
            <ol className="mt-2 flex gap-1" aria-label="Setup progress">
              {SETUP_STEPS.map((entry, index) => (
                <li
                  key={entry.id}
                  className="flex-1"
                  aria-current={index === currentStepIndex ? "step" : undefined}
                >
                  <span className="sr-only">
                    {entry.label}
                    {index < currentStepIndex
                      ? " (done)"
                      : index === currentStepIndex
                        ? " (current)"
                        : ""}
                  </span>
                  <span
                    aria-hidden="true"
                    className={`block h-1 rounded-full ${
                      index <= currentStepIndex ? "bg-action" : "bg-surface-inset"
                    }`}
                  />
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* No aria-live here: the error banner inside is already role="alert",
            and a live region wrapping the whole panel would announce it twice
            and re-read the step on every render. Focus does the work. */}
        <div
          ref={stepPanelRef}
          tabIndex={-1}
          className="p-6 sm:p-8 focus:outline-none"
        >
          {error && (
            <div
              id="setup-error"
              role="alert"
              className="mb-6 p-4 bg-status-danger-bg border border-status-danger-border rounded-lg flex items-start gap-3"
            >
              <AlertTriangle className="w-5 h-5 text-status-danger-fg shrink-0 mt-0.5" />
              <p className="text-sm text-status-danger-fg">{error}</p>
            </div>
          )}

          {ffmpegMissing && (
            <div className="mb-6 p-4 bg-status-warning-bg border border-status-warning-border rounded-lg flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-status-warning-fg shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-status-warning-fg">
                  FFmpeg not detected
                </p>
                <p className="text-sm text-status-warning-fg mt-1">
                  FFmpeg/FFprobe were not found in the server&apos;s PATH. Audio
                  processing will not work correctly until they are installed.
                </p>
              </div>
            </div>
          )}

          {step === STEP_UNLOCK && (
            <UnlockStep
              error={error}
              unlocking={unlocking}
              onBootstrapPasswordChange={handleBootstrapPasswordChange}
              onSubmit={handleUnlockSubmit}
            />
          )}

          {step === STEP_LEGAL && (
            <LegalStep
              onAccept={handleLegalSubmit}
              enableTelemetry={enableTelemetry}
              onEnableTelemetryChange={setEnableTelemetry}
            />
          )}

          {step === STEP_TRANSCRIPTION && (
            <TranscriptionStep
              whisperModelSize={formData.whisper_model_size}
              hfTokenPresent={hfTokenPresent}
              pyannoteModelsReady={pyannoteModelsReady}
              bundledPyannoteModelsReady={bundledPyannoteModelsReady}
              reloadingConfig={reloadingConfig}
              onInputChange={handleInputChange}
              onReloadConfig={handleReloadConfig}
              onBack={() => goToStep(STEP_LEGAL)}
              onSubmit={handleTranscriptionSubmit}
            />
          )}

          {step === STEP_ACCOUNT && (
            <AccountStep
              formData={formData}
              error={error}
              includeDemoRecording={includeDemoRecording}
              creatingAccount={creatingAccount}
              onSubmit={handleAccountSubmit}
              onInputChange={handleInputChange}
              onIncludeDemoRecordingChange={setIncludeDemoRecording}
              onBack={() => goToStep(STEP_TRANSCRIPTION)}
            />
          )}

          {step === STEP_AI && (
            <AiStep
              aiRoute={aiRoute}
              onRouteChange={setAiRoute}
              serverProvider={serverProvider}
              serverProviderSelected={serverProviderSelected}
              serverCredentialPresent={serverCredentialPresent}
              secondaryProvider={secondaryProvider}
              secondaryProviderConfigured={secondaryProviderConfigured}
              validatingLLM={validatingLLM}
              llmValidationMsg={llmValidationMsg}
              availableModels={availableModels}
              selectedModel={formData.selected_model}
              reloadingConfig={reloadingConfig}
              savingAi={savingAi}
              connectedCliProvider={connectedCliProvider}
              onCliStatusChange={setCliStatus}
              onInputChange={handleInputChange}
              onReloadConfig={handleReloadConfig}
              onSubmit={handleAiSubmit}
            />
          )}

          {step === STEP_COMPLETE && (
            <CompleteStep
              modelPreparationComplete={modelPreparationComplete}
              modelPreparationMessage={modelPreparationMessage}
              modelPreparationStage={modelPreparationStage}
              modelPreparationProgress={modelPreparationProgress}
              modelPreparationWarning={modelPreparationWarning}
              summary={{
                username: formData.username,
                whisperModelLabel,
                diarizationReady: hfTokenPresent || pyannoteModelsReady,
                aiRoute,
                aiDetail,
                demoRecording: includeDemoRecording,
              }}
              onComplete={handleCompleteSetup}
            />
          )}
        </div>
      </div>
    </div>
  );
}
