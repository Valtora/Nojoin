'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { getCurrentUser } from '@/lib/api';

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const checkAuth = async () => {
      const publicPaths = ['/login', '/setup', '/register'];
      
      let currentUser = null;
      try {
        currentUser = await getCurrentUser();
      } catch (e: any) {
        currentUser = null;
        if (
          !publicPaths.some(p => pathname?.startsWith(p)) &&
          e?.response?.status !== 401
        ) {
          console.error("Failed to validate current user", e);
          setError(e.message || "Failed to connect to server");
        }
      }
      
      if (!currentUser && !publicPaths.some(p => pathname?.startsWith(p))) {
        router.push('/login');
        return;
      }

      if (
        currentUser &&
        publicPaths.some(p => pathname?.startsWith(p))
      ) {
        router.push(
          currentUser.force_password_change
            ? '/settings?tab=account&forcePasswordChange=1'
            : '/',
        );
        return;
      }

      if (
        currentUser?.force_password_change &&
        !pathname?.startsWith('/settings')
      ) {
        router.push('/settings?tab=account&forcePasswordChange=1');
        return;
      }
      
      setChecked(true);
    };

    checkAuth();
  }, [pathname, router]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900 text-white p-4">
        <div className="bg-red-500/10 border border-red-500/50 text-red-500 p-6 rounded-lg max-w-md text-center">
          <h2 className="text-xl font-bold mb-2">Connection Error</h2>
          <p className="mb-4">{error}</p>
          <p className="text-sm text-gray-400">
            Please ensure the backend server is running and accessible at <br/>
            <code className="bg-black/30 px-1 rounded">https://localhost:14443/api/v1</code>
          </p>
          <button 
            onClick={() => window.location.reload()}
            className="mt-4 px-4 py-2 bg-red-600 hover:bg-red-700 rounded text-white text-sm font-medium"
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
