'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { getSystemStatus } from '@/lib/api';

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const checkAuth = async () => {
      // 1. Check if system is initialized
      try {
        const status = await getSystemStatus();
        if (!status.initialized && pathname !== '/setup') {
          router.push('/setup');
          return;
        }
        
        if (status.initialized && pathname === '/setup') {
            router.push('/login');
            return;
        }
      } catch (e: any) {
        console.error("Failed to check system status", e);
        setError(e.message || "Failed to connect to server");
        // Don't return here, let it try to check token, but the error will be shown
      }

      // 2. Check if user is authenticated
      const token = localStorage.getItem('token');
      const publicPaths = ['/login', '/setup'];
      
      if (!token && !publicPaths.includes(pathname || '')) {
        router.push('/login');
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
