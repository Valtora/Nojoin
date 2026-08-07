import Link from "next/link";
import { Lock } from "lucide-react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";

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
        <h2 className="text-xl font-semibold text-foreground">First-Run Setup</h2>
        <p className="text-sm text-contrast-helper mt-2">
          Unlock the setup wizard to initialise this Nojoin deployment
        </p>
      </div>

      <Input
        id="setup-unlock-password"
        type="password"
        name="setup-unlock-password"
        label="First-run setup password"
        autoComplete="off"
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        aria-describedby={error ? "setup-error" : undefined}
        aria-invalid={Boolean(error)}
        required
        iconLeft={<Lock />}
        onChange={onBootstrapPasswordChange}
        placeholder="Enter first-run setup password"
        hint="This is the FIRST_RUN_PASSWORD value from your deployment environment (.env). It is only used to initialise a new Nojoin system."
      />

      <Button
        type="submit"
        variant="primary"
        fullWidth
        loading={unlocking}
        className="mt-4"
      >
        {unlocking ? "Unlocking..." : "Unlock Setup"}
      </Button>

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
