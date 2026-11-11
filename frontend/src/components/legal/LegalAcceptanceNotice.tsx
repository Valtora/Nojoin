"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import LegalTerms from "@/components/legal/LegalTerms";
import Button from "@/components/ui/Button";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { acceptLegalTerms, logout } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { noteLegalAcceptance } from "@/lib/legalAcceptance";

/**
 * The terms of use, in place of the app, for an account that has not accepted
 * them (#1004). `AuthGuard` renders it instead of the page asked for, so no
 * URL reaches past it, and the page appears as it was once the terms are
 * accepted. It cannot be dismissed: the only other way on is to sign out.
 */
export default function LegalAcceptanceNotice() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [accepting, setAccepting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The notice replaces whatever had focus. Starting a screen reader and the
  // keyboard at its title says what changed and why.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const accept = async () => {
    setError(null);
    setAccepting(true);
    try {
      noteLegalAcceptance(await acceptLegalTerms());
    } catch (e: unknown) {
      setError(
        getErrorMessage(e, "Nojoin could not record your acceptance. Try again."),
      );
      setAccepting(false);
    }
  };

  const signOut = async () => {
    setSigningOut(true);
    await logout();
  };

  return (
    // page-shell: this view owns its scroll, because <body> is overflow-hidden
    // for the dashboard's sake.
    <div className="page-shell flex flex-col items-center bg-surface-page p-4">
      <SurfaceBase
        data-ui-fill="card"
        data-ui-border="full"
        kind="card"
        className="my-auto w-full max-w-xl shrink-0 overflow-hidden rounded-surface border border-surface-border bg-surface-card shadow-card"
      >
        <div className="flex items-center gap-3 bg-action px-5 py-3">
          <div className="shrink-0 rounded-full bg-action-on p-1.5">
            <Image
              src="/assets/NojoinLogo.png"
              alt=""
              width={24}
              height={24}
              className="h-6 w-6"
            />
          </div>
          <p className="text-base font-semibold text-action-on">Nojoin</p>
        </div>

        <main aria-labelledby="legal-notice-title" className="space-y-6 p-6 sm:p-8">
          <div>
            <h1
              id="legal-notice-title"
              ref={headingRef}
              tabIndex={-1}
              className="text-xl font-semibold text-foreground focus:outline-none"
            >
              Legal disclaimer
            </h1>
            <p className="mt-1 text-sm text-contrast-helper">
              Before you use Nojoin, read and accept its terms of use. You
              accept them once, for your own account.
            </p>
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-status-danger-border bg-status-danger-bg p-4 text-sm text-status-danger-fg"
            >
              {error}
            </div>
          )}

          <LegalTerms headingLevel={2} />

          <div className="space-y-3">
            <Button
              variant="primary"
              fullWidth
              onClick={accept}
              loading={accepting}
              disabled={signingOut}
            >
              I accept
            </Button>
            <Button
              variant="secondary"
              fullWidth
              onClick={signOut}
              loading={signingOut}
              disabled={accepting}
            >
              Sign out
            </Button>
          </div>
        </main>
      </SurfaceBase>
    </div>
  );
}
