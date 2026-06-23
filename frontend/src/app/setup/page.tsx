"use client";

import Image from "next/image";
import { Loader2, AlertTriangle } from "lucide-react";
import ConfirmationModal from "@/components/ConfirmationModal";

import AccountStep from "./_components/AccountStep";
import CompleteStep from "./_components/CompleteStep";
import HuggingFaceStep from "./_components/HuggingFaceStep";
import LegalStep from "./_components/LegalStep";
import LlmStep from "./_components/LlmStep";
import { useSetupWizard } from "./_hooks/useSetupWizard";

export default function SetupPage() {
  const {
    loading,
    step,
    formData,
    error,
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
      <div className="min-h-screen flex items-center justify-center bg-gray-200 dark:bg-gray-900">
        <Loader2 className="w-8 h-8 animate-spin text-orange-500" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-200 dark:bg-gray-900 p-4">
      <ConfirmationModal
        isOpen={showSkipLLMModal}
        onClose={() => setShowSkipLLMModal(false)}
        onConfirm={confirmSkipLLM}
        title="Skip AI Setup?"
        message="Without an AI provider and model, Nojoin will still record and transcribe meetings, but automatic meeting enhancement will be skipped. Generate Notes, meeting chat, and Retry Speaker Inference will work after you configure AI later in Settings."
        confirmText="Skip AI Configuration"
        isDangerous={true}
      />

      <div className="w-full max-w-md bg-white dark:bg-gray-800 rounded-2xl shadow-xl overflow-hidden">
        {/* Header */}
        <div className="bg-orange-600 p-6 text-center">
          <div className="flex justify-center mb-4">
            <div className="bg-white p-3 rounded-full shadow-lg">
              <Image
                src="/assets/NojoinLogo.png"
                alt="Nojoin"
                width={48}
                height={48}
                className="w-12 h-12"
              />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-white">Welcome to Nojoin</h1>
          <p className="text-orange-100 mt-2">Initial System Setup</p>
        </div>

        {/* Progress Steps */}
        <div className="flex border-b border-gray-200 dark:border-gray-700">
          {[0, 1, 2, 3, 4].map((s) => (
            <div
              key={s}
              className={`flex-1 h-1 ${s <= step ? "bg-blue-600" : "bg-gray-200 dark:bg-gray-700"}`}
            />
          ))}
        </div>

        <div className="p-8">
          {error && (
            <div
              id="setup-error"
              role="alert"
              aria-live="polite"
              className="mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-start gap-3"
            >
              <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            </div>
          )}

          {ffmpegMissing && (
            <div className="mb-6 p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-yellow-800 dark:text-yellow-300">
                  FFmpeg not detected
                </p>
                <p className="text-sm text-yellow-600 dark:text-yellow-400 mt-1">
                  FFmpeg/FFprobe were not found in your system PATH. Audio
                  processing features will not work correctly. Please install
                  FFmpeg and restart the application.
                </p>
              </div>
            </div>
          )}

          {/* Step 0: Legal Disclaimer */}
          {step === 0 && <LegalStep onAccept={handleLegalSubmit} />}

          {/* Step 1: Account */}
          {step === 1 && (
            <AccountStep
              formData={formData}
              error={error}
              onSubmit={handleAccountSubmit}
              onInputChange={handleInputChange}
              onBootstrapPasswordChange={handleBootstrapPasswordChange}
            />
          )}

          {/* Step 2: LLM Setup */}
          {step === 2 && (
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

          {/* Step 3: HuggingFace */}
          {step === 3 && (
            <HuggingFaceStep
              formData={formData}
              loading={loading}
              pyannoteModelsReady={pyannoteModelsReady}
              bundledPyannoteModelsReady={bundledPyannoteModelsReady}
              onReloadConfig={handleReloadConfig}
              onSubmit={handleHFSubmit}
            />
          )}

          {/* Step 4: Complete */}
          {step === 4 && (
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
