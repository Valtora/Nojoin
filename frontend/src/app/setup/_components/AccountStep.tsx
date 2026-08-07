import { ArrowLeft, ArrowRight } from "lucide-react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";

interface AccountStepProps {
  formData: {
    username: string;
    password: string;
    confirmPassword: string;
  };
  error: string;
  includeDemoRecording: boolean;
  creatingAccount: boolean;
  onSubmit: (e: React.FormEvent) => void;
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onIncludeDemoRecordingChange: (include: boolean) => void;
  onBack: () => void;
}

/**
 * Creates the owner account. This is the wizard's point of no return: the
 * submit handler creates the account and signs in, so every later step runs
 * authenticated and the Back button disappears from here on.
 */
export default function AccountStep({
  formData,
  error,
  includeDemoRecording,
  creatingAccount,
  onSubmit,
  onInputChange,
  onIncludeDemoRecordingChange,
  onBack,
}: AccountStepProps) {
  return (
    <form
      id="setup-admin-account-form"
      name="setup-admin-account-form"
      method="post"
      onSubmit={onSubmit}
      className="space-y-4"
      autoComplete="on"
    >
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-foreground">
          Create Admin Account
        </h2>
        <p className="text-sm text-contrast-helper">
          Set up your administrator credentials
        </p>
      </div>

      <Input
        id="setup-admin-username"
        type="text"
        name="setup-admin-username"
        data-field-key="username"
        label="Username"
        autoComplete="username"
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        aria-describedby={error ? "setup-error" : undefined}
        aria-invalid={Boolean(error)}
        required
        value={formData.username}
        onChange={onInputChange}
        placeholder="admin"
      />

      <Input
        id="setup-admin-new-password"
        type="password"
        name="setup-admin-new-password"
        data-field-key="password"
        label="Password"
        autoComplete="new-password"
        aria-describedby={error ? "setup-error" : undefined}
        aria-invalid={Boolean(error)}
        required
        minLength={8}
        value={formData.password}
        onChange={onInputChange}
        placeholder="••••••••"
        hint="At least 8 characters."
      />

      <Input
        id="setup-admin-confirm-password"
        type="password"
        name="setup-admin-confirm-password"
        data-field-key="confirmPassword"
        label="Confirm Password"
        autoComplete="new-password"
        aria-describedby={error ? "setup-error" : undefined}
        aria-invalid={Boolean(error)}
        required
        minLength={8}
        value={formData.confirmPassword}
        onChange={onInputChange}
        placeholder="••••••••"
      />

      <label
        htmlFor="setup-include-demo-recording"
        className="flex items-start gap-3 p-3 rounded-lg border border-surface-border bg-surface-inset/40 cursor-pointer"
      >
        <input
          id="setup-include-demo-recording"
          type="checkbox"
          name="setup-include-demo-recording"
          checked={includeDemoRecording}
          onChange={(e) => onIncludeDemoRecordingChange(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-control-border text-action-text accent-action focus-visible:outline-focus-ring"
        />
        <span className="text-sm text-contrast-muted">
          Include a sample meeting
          <span className="block text-xs text-contrast-helper mt-0.5">
            A short recording with its transcript, notes, and chat already in
            place. Needs no AI provider. Removable in Settings &gt; Help.
          </span>
        </span>
      </label>

      <div className="flex gap-3 pt-2">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={creatingAccount}
          iconLeft={<ArrowLeft className="w-4 h-4" />}
        >
          Back
        </Button>
        <Button
          type="submit"
          variant="primary"
          className="flex-1"
          loading={creatingAccount}
          iconRight={<ArrowRight className="w-4 h-4" />}
        >
          {creatingAccount ? "Creating account..." : "Create account"}
        </Button>
      </div>
    </form>
  );
}
