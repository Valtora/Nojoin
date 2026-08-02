"use client";

import axios from "axios";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { login, getCurrentUser } from "@/lib/api";
import { Lock, User } from "lucide-react";

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
  const router = useRouter();

  useEffect(() => {
    const checkCurrentUser = async () => {
      try {
        const user = await getCurrentUser();
        router.push(
          user.force_password_change
            ? "/settings/profile"
            : "/",
        );
        return;
      } catch {
        // no-op, user is not logged in
      }
    };

    checkCurrentUser();
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await login(username, password);

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

  return (
    <div className="min-h-dvh flex items-center justify-center bg-surface-page px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8 p-10 bg-surface-card rounded-surface border border-surface-border shadow-card">
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
        <form
          id="login-form"
          name="login-form"
          method="post"
          className="mt-8 space-y-6"
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
              <input
                id="login-username"
                name="username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                aria-describedby={error ? "login-error" : undefined}
                aria-invalid={Boolean(error)}
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
              <input
                id="login-current-password"
                name="password"
                type="password"
                autoComplete="current-password"
                aria-describedby={error ? "login-error" : undefined}
                aria-invalid={Boolean(error)}
                required
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>

          {error && (
            <div
              id="login-error"
              role="alert"
              aria-live="polite"
              className="text-status-danger-fg text-sm text-center bg-status-danger-bg p-3 rounded-lg border border-status-danger-border"
            >
              {error}
            </div>
          )}

          <div>
            <button
              type="submit"
              disabled={loading}
              className="group relative w-full flex justify-center py-3 px-4 border border-transparent text-sm font-semibold rounded-lg text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
