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
        <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
          First-Run Setup
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
          Unlock the setup wizard to initialise this Nojoin deployment
        </p>
      </div>

      <div>
        <label
          htmlFor="setup-unlock-password"
          className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
        >
          First-run setup password
        </label>
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <Lock className="h-5 w-5 text-gray-500 dark:text-gray-400" />
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
            className="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none"
            placeholder="Enter first-run setup password"
          />
        </div>
        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
          This is the FIRST_RUN_PASSWORD value from your deployment environment
          (.env). It is only used to initialise a new Nojoin system.
        </p>
      </div>

      <button
        type="submit"
        disabled={unlocking}
        className="w-full mt-4 bg-orange-600 hover:bg-orange-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {unlocking ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" /> Unlocking...
          </>
        ) : (
          "Unlock Setup"
        )}
      </button>

      <p className="text-center text-sm text-gray-600 dark:text-gray-300 mt-2">
        Already set up?{" "}
        <Link
          href="/login"
          className="font-medium text-orange-600 hover:text-orange-500"
        >
          Back to sign in
        </Link>
      </p>
    </form>
  );
}
