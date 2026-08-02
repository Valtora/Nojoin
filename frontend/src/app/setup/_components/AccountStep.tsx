import { ArrowRight } from "lucide-react";

interface AccountStepProps {
  formData: {
    username: string;
    password: string;
    confirmPassword: string;
  };
  error: string;
  includeDemoRecording: boolean;
  onSubmit: (e: React.FormEvent) => void;
  onInputChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>,
  ) => void;
  onIncludeDemoRecordingChange: (include: boolean) => void;
}

export default function AccountStep({
  formData,
  error,
  includeDemoRecording,
  onSubmit,
  onInputChange,
  onIncludeDemoRecordingChange,
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

      <div>
        <label htmlFor="setup-admin-username" className="block text-sm font-medium text-contrast-muted mb-1">
          Username
        </label>
        <input
          id="setup-admin-username"
          type="text"
          name="setup-admin-username"
          data-field-key="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby={error ? "setup-error" : undefined}
          aria-invalid={Boolean(error)}
          required
          value={formData.username}
          onChange={onInputChange}
          className="w-full px-4 py-2 rounded-lg border border-control-border bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
          placeholder="admin"
        />
      </div>

      <div>
        <label htmlFor="setup-admin-new-password" className="block text-sm font-medium text-contrast-muted mb-1">
          Password
        </label>
        <input
          id="setup-admin-new-password"
          type="password"
          name="setup-admin-new-password"
          data-field-key="password"
          autoComplete="new-password"
          aria-describedby={error ? "setup-error" : undefined}
          aria-invalid={Boolean(error)}
          required
          minLength={8}
          value={formData.password}
          onChange={onInputChange}
          className="w-full px-4 py-2 rounded-lg border border-control-border bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
          placeholder="••••••••"
        />
      </div>

      <div>
        <label htmlFor="setup-admin-confirm-password" className="block text-sm font-medium text-contrast-muted mb-1">
          Confirm Password
        </label>
        <input
          id="setup-admin-confirm-password"
          type="password"
          name="setup-admin-confirm-password"
          data-field-key="confirmPassword"
          autoComplete="new-password"
          aria-describedby={error ? "setup-error" : undefined}
          aria-invalid={Boolean(error)}
          required
          minLength={8}
          value={formData.confirmPassword}
          onChange={onInputChange}
          className="w-full px-4 py-2 rounded-lg border border-control-border bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
          placeholder="••••••••"
        />
      </div>

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
          className="mt-0.5 h-4 w-4 rounded border-control-border text-action-text focus-visible:outline-focus-ring"
        />
        <span className="text-sm text-contrast-muted">
          Include a sample meeting
          <span className="block text-xs text-contrast-helper mt-0.5">
            Adds a short &quot;Welcome to Nojoin&quot; recording so you can
            explore transcripts, notes, and speakers. You can remove or
            recreate it later in Settings &gt; Help.
          </span>
        </span>
      </label>

      <button
        type="submit"
        className="w-full mt-6 bg-action hover:bg-action-hover text-action-on font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
      >
        Next Step <ArrowRight className="w-4 h-4" />
      </button>
    </form>
  );
}
