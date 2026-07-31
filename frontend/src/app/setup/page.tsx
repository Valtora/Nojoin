"use client";

import Image from "next/image";
import { Loader2, AlertTriangle } from "lucide-react";
import ConfirmationModal from "@/components/ConfirmationModal";

import AccountStep from "./_components/AccountStep";
import CompleteStep from "./_components/CompleteStep";
import HuggingFaceStep from "./_components/HuggingFaceStep";
import LegalStep from "./_components/LegalStep";
import LlmStep from "./_components/LlmStep";
import UnlockStep from "./_components/UnlockStep";
import { useSetupWizard } from "./_hooks/useSetupWizard";

export default function SetupPage() {
  const {
    loading,
    step,
    formData,
    error,
    unlocking,
    includeDemoRecording,
    enableTelemetry,
    setEnableTelemetry,
    setIncludeDemoRecording,
    ffmpegMissing,
    showSkipLLMModal,
    setShowSkipLLMModal,
    validatingLLM,
    llmValidationMsg,
    llmConfigMissing,
    availableModels,
    pyannoteModelsReady,
    bundledPyannoteModelsReady,
    modelPreparationProgress,
    modelPreparationMessage,
    modelPreparationStage,
    modelPreparationComplete,
    handleInputChange,
    handleBootstrapPasswordChange,
    handleUnlockSubmit,
    handleLegalSubmit,
    handleAccountSubmit,
    handleLLMSubmit,
    handleSkipLLM,
    confirmSkipLLM,
    handleReloadConfig,
    handleHFSubmit,
    handleCompleteSetup,
  } = useSetupWizard();

  if (loading) {
    return (
      <div className="min-h-dvh flex items-center justify-center bg-surface-page">
        <Loader2 className="w-8 h-8 animate-spin text-action-text" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh flex flex-col items-center justify-center bg-surface-page p-4">
      <ConfirmationModal
        isOpen={showSkipLLMModal}
        onClose={() => setShowSkipLLMModal(false)}
        onConfirm={confirmSkipLLM}
        title="Skip AI Setup?"
        message="Without an AI provider and model, Nojoin will still record and transcribe meetings, but automatic meeting enhancement will be skipped. Generate Notes, meeting chat, and Retry Speaker Inference will work after you configure AI later in Settings."
        confirmText="Skip AI Configuration"
        isDangerous={true}
      />

      <div className="w-full max-w-md bg-surface-card rounded-surface border border-surface-border shadow-card overflow-hidden">
        {/* Header */}
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
          <h1 className="text-2xl font-bold text-action-on">Welcome to Nojoin</h1>
          <p className="text-action-on-muted mt-2">Initial System Setup</p>
        </div>

        {/* Progress Steps (hidden on the unlock gate) */}
        {step > 0 && (
          <div className="flex border-b border-surface-divider">
            {[1, 2, 3, 4, 5].map((s) => (
              <div
                key={s}
                className={`flex-1 h-1 ${s <= step ? "bg-action" : "bg-surface-inset"}`}
              />
            ))}
          </div>
        )}

        <div className="p-8">
          {error && (
            <div
              id="setup-error"
              role="alert"
              aria-live="polite"
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
                  FFmpeg/FFprobe were not found in your system PATH. Audio
                  processing features will not work correctly. Please install
                  FFmpeg and restart the application.
                </p>
              </div>
            </div>
          )}

          {/* Step 0: Unlock Gate */}
          {step === 0 && (
            <UnlockStep
              error={error}
              unlocking={unlocking}
              onBootstrapPasswordChange={handleBootstrapPasswordChange}
              onSubmit={handleUnlockSubmit}
            />
          )}

          {/* Step 1: Legal Disclaimer */}
          {step === 1 && (
            <LegalStep
              onAccept={handleLegalSubmit}
              enableTelemetry={enableTelemetry}
              onEnableTelemetryChange={setEnableTelemetry}
            />
          )}

          {/* Step 2: Account */}
          {step === 2 && (
            <AccountStep
              formData={formData}
              error={error}
              includeDemoRecording={includeDemoRecording}
              onSubmit={handleAccountSubmit}
              onInputChange={handleInputChange}
              onIncludeDemoRecordingChange={setIncludeDemoRecording}
            />
          )}

          {/* Step 3: LLM Setup */}
          {step === 3 && (
            <LlmStep
              formData={formData}
              loading={loading}
              llmConfigMissing={llmConfigMissing}
              validatingLLM={validatingLLM}
              llmValidationMsg={llmValidationMsg}
              availableModels={availableModels}
              onInputChange={handleInputChange}
              onReloadConfig={handleReloadConfig}
              onConfirmSkip={confirmSkipLLM}
              onSkip={handleSkipLLM}
              onSubmit={handleLLMSubmit}
            />
          )}

          {/* Step 4: Transcription & Speaker Models */}
          {step === 4 && (
            <HuggingFaceStep
              formData={formData}
              loading={loading}
              pyannoteModelsReady={pyannoteModelsReady}
              bundledPyannoteModelsReady={bundledPyannoteModelsReady}
              onInputChange={handleInputChange}
              onReloadConfig={handleReloadConfig}
              onSubmit={handleHFSubmit}
            />
          )}

          {/* Step 5: Complete */}
          {step === 5 && (
            <CompleteStep
              modelPreparationComplete={modelPreparationComplete}
              modelPreparationMessage={modelPreparationMessage}
              modelPreparationStage={modelPreparationStage}
              modelPreparationProgress={modelPreparationProgress}
              error={error}
              onComplete={handleCompleteSetup}
            />
          )}
        </div>
      </div>
    </div>
  );
}
