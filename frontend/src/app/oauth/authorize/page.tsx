"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Image from "next/image";
import {
  FileText,
  Link2,
  Loader2,
  Lock,
  Mic,
  Plug,
  ShieldCheck,
  Tag,
  User as UserIcon,
  UserPlus,
  Users,
} from "lucide-react";

import {
  getCurrentUser,
  getOAuthAuthorizeInfo,
  login,
  submitOAuthDecision,
  type OAuthAuthorizeInfo,
  type OAuthAuthorizeParams,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";

// Known connector clients get a real product mark on the consent screen;
// anything else falls back to a neutral plug badge.
function resolveClientLogo(clientName: string): string | null {
  if (clientName.toLowerCase().includes("claude")) {
    return "/assets/connectors/claude.png";
  }
  return null;
}

// Human-readable consent copy per scope; an unknown scope falls back to
// its raw name so the user is never shown less than what is requested.
const SCOPE_CAPABILITIES: Record<
  string,
  { icon: typeof Mic; label: string }[]
> = {
  "mcp:read": [
    { icon: Mic, label: "View your recordings" },
    { icon: FileText, label: "Read transcripts, notes, and attached documents" },
    { icon: Users, label: "See meeting speakers and your People library" },
    { icon: Tag, label: "See your tags" },
  ],
  "mcp:write": [
    { icon: UserPlus, label: "Add or update people in your People library" },
    { icon: Users, label: "Name meeting speakers and link them to people" },
    { icon: FileText, label: "Append to a meeting's notes" },
  ],
};

function redirectHost(redirectUri: string): string | null {
  try {
    return new URL(redirectUri).hostname;
  } catch {
    return null;
  }
}

const inputClasses =
  "appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors";

function AuthorizeContent() {
  const searchParams = useSearchParams();

  const params: OAuthAuthorizeParams | null = useMemo(() => {
    const client_id = searchParams.get("client_id");
    const redirect_uri = searchParams.get("redirect_uri");
    if (!client_id || !redirect_uri) {
      return null;
    }
    return {
      client_id,
      redirect_uri,
      response_type: searchParams.get("response_type") ?? "code",
      scope: searchParams.get("scope") ?? undefined,
      state: searchParams.get("state") ?? undefined,
      code_challenge: searchParams.get("code_challenge") ?? undefined,
      code_challenge_method:
        searchParams.get("code_challenge_method") ?? undefined,
      resource: searchParams.get("resource") ?? undefined,
    };
  }, [searchParams]);

  const [loading, setLoading] = useState(true);
  const [signedInUsername, setSignedInUsername] = useState<string | null>(null);
  const [info, setInfo] = useState<OAuthAuthorizeInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const initialise = async () => {
      if (!params) {
        setError(
          "This authorisation link is incomplete. Ask the connecting app to retry.",
        );
        setLoading(false);
        return;
      }
      try {
        const authorizeInfo = await getOAuthAuthorizeInfo(params);
        setInfo(authorizeInfo);
      } catch (e: unknown) {
        setError(getErrorMessage(e, "This authorisation request is invalid."));
        setLoading(false);
        return;
      }
      try {
        const user = await getCurrentUser();
        setSignedInUsername(user.username);
      } catch {
        setSignedInUsername(null);
      }
      setLoading(false);
    };
    initialise();
  }, [params]);

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSigningIn(true);
    try {
      const response = await login(username, password);
      if (response.force_password_change) {
        window.location.href = "/settings/profile";
        return;
      }
      setSignedInUsername(response.username);
    } catch (e: unknown) {
      setError(getErrorMessage(e, "Sign-in failed. Please try again."));
    } finally {
      setSigningIn(false);
    }
  };

  const handleDecision = async (approve: boolean) => {
    if (!params) {
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const result = await submitOAuthDecision(params, approve);
      window.location.href = result.redirect_to;
    } catch (e: unknown) {
      setError(getErrorMessage(e, "Unable to complete the authorisation."));
      setSubmitting(false);
    }
  };

  const clientLogo = info ? resolveClientLogo(info.client_name) : null;
  const returnHost = params ? redirectHost(params.redirect_uri) : null;
  const isReadOnly = Boolean(
    info && info.scope_items.every((scope) => scope === "mcp:read"),
  );
  const capabilities = info
    ? info.scope_items.flatMap(
        (scope) =>
          SCOPE_CAPABILITIES[scope] ?? [{ icon: ShieldCheck, label: scope }],
      )
    : [];

  return (
    <div className="min-h-dvh flex flex-col items-center justify-center bg-surface-page px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full bg-surface-card rounded-surface border border-surface-border shadow-card overflow-hidden">
        <div className="px-8 pt-10 pb-8">
          {loading && (
            <div className="flex justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-action-text" />
            </div>
          )}

          {!loading && error && !info && (
            <div className="text-center space-y-4 py-6">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-surface-panel bg-status-danger-bg">
                <Plug className="h-6 w-6 text-danger-text" />
              </div>
              <p className="text-sm text-contrast-muted">{error}</p>
            </div>
          )}

          {!loading && info && (
            <>
              {/* App-to-app connection header */}
              <div className="flex items-center justify-center gap-4">
                <div className="flex h-16 w-16 items-center justify-center rounded-surface-panel bg-surface-inset overflow-hidden">
                  {clientLogo ? (
                    <Image
                      src={clientLogo}
                      alt={`${info.client_name} logo`}
                      width={64}
                      height={64}
                      className="object-cover"
                    />
                  ) : (
                    <Plug className="h-7 w-7 text-contrast-icon-muted" />
                  )}
                </div>
                <Link2 className="h-4 w-4 text-contrast-icon-muted" />
                <div className="flex h-16 w-16 items-center justify-center rounded-surface-panel bg-surface-inset">
                  <Image
                    src="/assets/NojoinLogo.png"
                    alt="Nojoin logo"
                    width={40}
                    height={40}
                    className="object-contain"
                  />
                </div>
              </div>

              <div className="mt-6 text-center">
                <h1 className="text-xl font-semibold text-foreground">
                  Connect {info.client_name} to Nojoin
                </h1>
                <p className="mt-1.5 text-sm text-contrast-helper">
                  {info.client_name} will be able to:
                </p>
              </div>

              {/* Capability list */}
              <ul className="mt-5 divide-y divide-surface-divider rounded-surface-panel border border-surface-border bg-surface-inset">
                {capabilities.map(({ icon: Icon, label }) => (
                  <li
                    key={label}
                    className="flex items-center gap-3 px-4 py-3 text-sm text-contrast-muted"
                  >
                    <Icon className="h-4 w-4 shrink-0 text-action-text" />
                    <span>{label}</span>
                  </li>
                ))}
              </ul>

              <p className="mt-3 flex items-start gap-2 text-xs text-contrast-helper">
                <ShieldCheck className="h-3.5 w-3.5 mt-0.5 shrink-0 text-status-success-fg" />
                {isReadOnly ? (
                  <>
                    Read-only access. {info.client_name} cannot change or
                    delete anything in Nojoin.
                  </>
                ) : (
                  <>
                    Write access is additive: {info.client_name} can add
                    people, name speakers, and append notes, but cannot delete
                    anything or change your recordings and transcripts.
                  </>
                )}
              </p>

              {error && (
                <p
                  className="mt-4 text-sm text-danger-text text-center"
                  role="alert"
                >
                  {error}
                </p>
              )}

              {!signedInUsername ? (
                <form className="mt-6 space-y-4" onSubmit={handleSignIn}>
                  <p className="text-sm font-medium text-foreground text-center">
                    Sign in to continue
                  </p>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <UserIcon className="h-5 w-5 text-contrast-icon-muted" />
                    </div>
                    <input
                      id="oauth-username"
                      name="username"
                      type="text"
                      autoComplete="username"
                      autoCapitalize="none"
                      required
                      className={inputClasses}
                      placeholder="Username"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </div>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                      <Lock className="h-5 w-5 text-contrast-icon-muted" />
                    </div>
                    <input
                      id="oauth-password"
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      required
                      className={inputClasses}
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={signingIn}
                    className="w-full inline-flex items-center justify-center gap-2 py-3 px-4 rounded-lg text-sm font-semibold text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 transition-colors"
                  >
                    {signingIn && <Loader2 className="h-4 w-4 animate-spin" />}
                    {signingIn ? "Signing in..." : "Sign in"}
                  </button>
                </form>
              ) : (
                <div className="mt-6 space-y-5">
                  <div className="flex items-center justify-center gap-2.5 rounded-lg border border-surface-border px-4 py-2.5">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-action-tint text-xs font-semibold text-action-tint-fg">
                      {signedInUsername.charAt(0).toUpperCase()}
                    </span>
                    <span className="text-sm text-contrast-helper">
                      Signed in as{" "}
                      <span className="font-medium text-foreground">
                        {signedInUsername}
                      </span>
                    </span>
                  </div>
                  <div className="flex gap-3">
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => handleDecision(false)}
                      className="flex-1 py-3 px-4 rounded-lg text-sm font-semibold text-foreground border border-control-border hover:bg-surface-inset focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 transition-colors"
                    >
                      Deny
                    </button>
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => handleDecision(true)}
                      className="flex-1 inline-flex items-center justify-center gap-2 py-3 px-4 rounded-lg text-sm font-semibold text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 transition-colors"
                    >
                      {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                      {submitting ? "Connecting..." : "Allow"}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {!loading && info && (
          <div className="border-t border-surface-divider bg-surface-inset px-8 py-4 space-y-1 text-center">
            {returnHost && (
              <p className="text-xs text-contrast-helper">
                You will be returned to{" "}
                <span className="font-medium text-contrast-muted">
                  {returnHost}
                </span>
                .
              </p>
            )}
            <p className="text-xs text-contrast-icon-muted">
              You can revoke this connection at any time in Settings &rsaquo;
              Connected Apps.
            </p>
          </div>
        )}
      </div>

      <p className="mt-6 text-xs text-contrast-icon-muted">
        Secured by your Nojoin server
      </p>
    </div>
  );
}

export default function OAuthAuthorizePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-dvh flex items-center justify-center bg-surface-page">
          <Loader2 className="h-6 w-6 animate-spin text-action-text" />
        </div>
      }
    >
      <AuthorizeContent />
    </Suspense>
  );
}
