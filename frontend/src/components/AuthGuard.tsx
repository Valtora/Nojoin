'use client';

import { useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { getSystemStatus } from '@/lib/api';

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

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
      } catch (e) {
        console.error("Failed to check system status", e);
      }

      // 2. Check if user is authenticated
      const token = localStorage.getItem('token');
      const publicPaths = ['/login', '/setup'];
      
      if (!token && !publicPaths.includes(pathname || '')) {
        router.push('/login');
      } 
      // Removed the auto-redirect from /login to / because it prevents re-login if token is stale
      // else if (token && pathname === '/login') {
      //   router.push('/');
      // }
      
      setChecked(true);
    };

    checkAuth();
  }, [pathname, router]);

  if (!checked) {
      return null; // Or a loading spinner
  }

  return <>{children}</>;
}
