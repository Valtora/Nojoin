'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { getCurrentUser } from '@/lib/api';
import { getErrorMessage, getErrorStatus } from '@/lib/errors';

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const checkAuth = async () => {
      const publicPaths = ['/login', '/setup', '/register'];
      // Neutral paths are reachable signed-in or signed-out. The OAuth
      // consent page renders its own inline sign-in so the authorization
      // parameters survive instead of being lost on a /login redirect.
      const neutralPaths = ['/oauth/authorize'];
      const isNeutralPath = neutralPaths.some(p => pathname?.startsWith(p));

      let currentUser = null;
      try {
        currentUser = await getCurrentUser();

            } catch (e: unknown) {
        currentUser = null;
        if (
          !publicPaths.some(p => pathname?.startsWith(p)) &&
          !isNeutralPath &&
          getErrorStatus(e) !== 401
        ) {
          console.error("Failed to validate current user", e);
          setError(getErrorMessage(e, "Failed to connect to server"));
        }
      }

      if (
        !currentUser &&
        !publicPaths.some(p => pathname?.startsWith(p)) &&
        !isNeutralPath
      ) {
        router.push('/login');
        return;
      }

      if (
        currentUser &&
        publicPaths.some(p => pathname?.startsWith(p))
      ) {
        router.push(
          currentUser.force_password_change
            ? '/settings/profile'
            : '/',
        );
        return;
      }

      if (
        currentUser?.force_password_change &&
        !pathname?.startsWith('/settings')
      ) {
        router.push('/settings/profile');
        return;
      }

      setChecked(true);
    };

    checkAuth();
  }, [pathname, router]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface-card text-foreground p-4">
        <div className="bg-status-danger-bg border border-status-danger-border text-status-danger-fg p-6 rounded-lg max-w-md text-center">
          <h2 className="text-xl font-bold mb-2">Connection Error</h2>
          <p className="mb-4">{error}</p>
          <p className="text-sm text-contrast-icon-muted">
            Please ensure the backend server is running and accessible at <br/>
            <code className="bg-surface-inset px-1 rounded">https://localhost:14443/api/v1</code>
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-status-danger-bg hover:bg-status-danger-bg rounded text-foreground text-sm font-medium"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!checked) {
      return null; // Or a loading spinner
  }

  return <>{children}</>;
}
