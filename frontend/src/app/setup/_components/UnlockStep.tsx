import Link from "next/link";
import { Loader2, Lock } from "lucide-react";

interface UnlockStepProps {
  error: string;
  unlocking: boolean;
  onBootstrapPasswordChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSubmit: (e: React.FormEvent) => void;
}

// The unlock gate is rendered identically whether or not the system is
// initialised; only a correct FIRST_RUN_PASSWORD on an uninitialised system
// advances past it, so the page never discloses initialisation state.
export default function UnlockStep({
  error,
  unlocking,
  onBootstrapPasswordChange,
  onSubmit,
}: UnlockStepProps) {
  return (
    <form
      id="setup-unlock-form"
      name="setup-unlock-form"
      method="post"
      onSubmit={onSubmit}
      className="space-y-4"
      autoComplete="off"
    >
      <div className="text-center mb-6">
        <h2 className="text-xl font-semibold text-foreground">
          First-Run Setup
        </h2>
        <p className="text-sm text-contrast-helper mt-2">
          Unlock the setup wizard to initialise this Nojoin deployment
        </p>
      </div>

      <div>
        <label
          htmlFor="setup-unlock-password"
          className="block text-sm font-medium text-contrast-muted mb-1"
        >
          First-run setup password
        </label>
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <Lock className="h-5 w-5 text-contrast-helper" />
          </div>
          <input
            id="setup-unlock-password"
            type="password"
            name="setup-unlock-password"
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-describedby={error ? "setup-error" : undefined}
            aria-invalid={Boolean(error)}
            required
            onChange={onBootstrapPasswordChange}
            className="w-full pl-10 pr-4 py-2 rounded-lg border border-control-border bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
            placeholder="Enter first-run setup password"
          />
        </div>
        <p className="mt-2 text-xs text-contrast-helper">
          This is the FIRST_RUN_PASSWORD value from your deployment environment
          (.env). It is only used to initialise a new Nojoin system.
        </p>
      </div>

      <button
        type="submit"
        disabled={unlocking}
        className="w-full mt-4 bg-action hover:bg-action-hover text-action-on font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {unlocking ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" /> Unlocking...
          </>
        ) : (
          "Unlock Setup"
        )}
      </button>

      <p className="text-center text-sm text-contrast-helper mt-2">
        Already set up?{" "}
        <Link
          href="/login"
          className="font-medium text-action-text hover:text-action-text-hover"
        >
          Back to sign in
        </Link>
      </p>
    </form>
  );
}
