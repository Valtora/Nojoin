"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import axios from "axios";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  login,
  getCurrentUser,
  getLoginMethods,
  ssoErrorMessage,
  ssoSignInUrl,
  type LoginMethods,
} from "@/lib/api";
import { DEMO_MODE, DEMO_PASSWORD, DEMO_USERNAME } from "@/lib/demo";
import { KeyRound, Lock, User } from "lucide-react";
import { loadingButtonProps } from "@/components/ui/loadingButton";
import { useLocationSearchParam } from "@/lib/useLocationSearchParam";

function formatLoginError(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return "Sign-in failed. Please try again.";
  }

  const detail = error.response?.data?.detail;
  if (typeof detail === "string" && detail.length > 0) {
    if (detail === "Incorrect username or password") {
      return "Invalid username or password";
    }
    return detail;
  }

  if (!error.response) {
    return "Nojoin backend is unavailable. Check the API container logs and try again.";
  }

  if (error.response.status >= 500) {
    return "Nojoin backend is unavailable. Check the API container logs and try again.";
  }

  return "Sign-in failed. Please try again.";
}

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [methods, setMethods] = useState<LoginMethods | null>(null);
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const router = useRouter();

  // An SSO failure comes back as /login?error=<code>. A password attempt's
  // own error replaces it.
  const ssoErrorCode = useLocationSearchParam("error");
  const shownError = error || (ssoErrorCode ? ssoErrorMessage(ssoErrorCode) : "");

  useEffect(() => {
    getLoginMethods()
      .then(setMethods)
      .catch(() => {
        // An older server has no /login/methods: offer the password form.
        setMethods(null);
      });
  }, []);

  const ssoEnabled = Boolean(methods?.oidc.enabled);
  const passwordPolicy = methods?.password_policy ?? "enabled";
  // With SSO on, a password form that only the owner may use is tucked away
  // behind a link, so everyone else is not tempted to try it.
  const passwordFormVisible =
    passwordPolicy === "enabled" ||
    (passwordPolicy === "owner_only" && (showPasswordForm || !ssoEnabled));

  useEffect(() => {
    const checkCurrentUser = async () => {
      try {
        const user = await getCurrentUser();
        router.push(user.force_password_change ? "/settings/profile" : "/");
        return;
      } catch {
        // no-op, user is not logged in
      }
    };

    checkCurrentUser();
  }, [router]);

  const signIn = async (name: string, secret: string) => {
    setError("");
    setLoading(true);

    try {
      const response = await login(name, secret);

      if (response.force_password_change) {
        router.push("/settings/profile");
        return;
      }

      router.push("/");
    } catch (error: unknown) {
      setError(formatLoginError(error));
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void signIn(username, password);
  };

  return (
    // page-shell: this route owns its scroll; <body> is overflow-hidden. See
    // the note on .page-shell in globals.css.
    <div className="page-shell flex flex-col items-center bg-surface-page px-4 py-8 sm:px-6 lg:px-8">
      <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="my-auto max-w-md w-full space-y-8 p-10 bg-surface-card rounded-surface border border-surface-border shadow-card">
        <div className="flex flex-col items-center justify-center">
          <div className="flex flex-col items-center gap-4 mb-2">
            <Image
              src="/assets/NojoinLogo.png"
              alt="Nojoin Logo"
              width={68}
              height={68}
              className="object-contain"
            />
            <h2 className="text-3xl font-bold text-action-text">Nojoin</h2>
          </div>
        </div>
        {DEMO_MODE ? (
          <div className="mt-8 space-y-6 text-center">
            <p className="text-sm text-contrast-muted">
              A read-only demo with sample meetings. Look around the dashboard,
              transcripts, notes and analytics; recording and AI are switched
              off.
            </p>
            {error && (
              <div
                role="alert"
                aria-live="polite"
                className="text-status-danger-fg text-sm bg-status-danger-bg p-3 rounded-lg border border-status-danger-border"
              >
                {error}
              </div>
            )}
            <ButtonBase
              data-ui-fill="action"
              data-ui-hover="action-hover"
              data-ui-border="full" chrome="action"
              type="button"
              {...loadingButtonProps(loading, undefined, () => void signIn(DEMO_USERNAME, DEMO_PASSWORD))}
              className="w-full flex justify-center py-3 px-4 border border-transparent text-sm font-semibold rounded-lg text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 disabled:cursor-not-allowed transition-colors aria-disabled:opacity-60 aria-disabled:cursor-not-allowed"
            >
              {loading ? "Opening the demo..." : "Enter the demo"}
            </ButtonBase>
          </div>
        ) : (
          <div className="mt-8 space-y-6">
          {ssoEnabled ? (
            <a
              href={ssoSignInUrl("/")}
              className="w-full flex items-center justify-center gap-2 py-3 px-4 border border-transparent text-sm font-semibold rounded-lg text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring transition-colors"
            >
              <KeyRound aria-hidden="true" className="h-4 w-4" />
              Sign in with {methods?.oidc.display_name ?? "SSO"}
            </a>
          ) : null}
          {shownError && !passwordFormVisible ? (
            <div
              role="alert"
              aria-live="polite"
              className="text-status-danger-fg text-sm text-center bg-status-danger-bg p-3 rounded-lg border border-status-danger-border"
            >
              {shownError}
            </div>
          ) : null}
          {ssoEnabled && passwordPolicy === "owner_only" && !showPasswordForm ? (
            <button
              type="button"
              onClick={() => setShowPasswordForm(true)}
              className="block w-full text-center text-sm text-contrast-muted underline-offset-4 hover:underline"
            >
              Sign in with a local password
            </button>
          ) : null}
          {passwordFormVisible ? (
          <form
            id="login-form"
            name="login-form"
            method="post"
            className="space-y-6"
            onSubmit={handleSubmit}
            autoComplete="on"
          >
            <div className="space-y-4">
              <div className="relative">
                <label htmlFor="login-username" className="sr-only">
                  Username
                </label>
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <User className="h-5 w-5 text-contrast-icon-muted" />
                </div>
                <InputBase
                  data-ui-fill="control"
                  data-ui-border="full"
                  id="login-username"
                  name="username"
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={shownError ? "login-error" : undefined}
                  aria-invalid={Boolean(shownError)}
                  required
                  className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                  placeholder="Username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
              <div className="relative">
                <label htmlFor="login-current-password" className="sr-only">
                  Password
                </label>
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock className="h-5 w-5 text-contrast-icon-muted" />
                </div>
                <InputBase
                  data-ui-fill="control"
                  data-ui-border="full"
                  id="login-current-password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  aria-describedby={shownError ? "login-error" : undefined}
                  aria-invalid={Boolean(shownError)}
                  required
                  className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>

            {shownError && (
              <div
                id="login-error"
                role="alert"
                aria-live="polite"
                className="text-status-danger-fg text-sm text-center bg-status-danger-bg p-3 rounded-lg border border-status-danger-border"
              >
                {shownError}
              </div>
            )}

            <div>
              <ButtonBase
                data-ui-fill="action"
                data-ui-hover="action-hover"
                data-ui-border="full" chrome="action"
                type="submit"
                {...loadingButtonProps(loading)}
                className="group relative w-full flex justify-center py-3 px-4 border border-transparent text-sm font-semibold rounded-lg text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 disabled:cursor-not-allowed transition-colors aria-disabled:opacity-60 aria-disabled:cursor-not-allowed"
              >
                {loading ? "Signing in..." : "Sign in"}
              </ButtonBase>
            </div>
          </form>
          ) : null}
          </div>
        )}
      </SurfaceBase>
    </div>
  );
}
