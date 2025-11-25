"use client";

import { useState, useEffect } from 'react';

interface HealthStatus {
  status: string;
  version: string;
  components: {
    db: string;
    worker: string;
  };
}

interface ServiceStatus {
  backend: boolean;
  db: boolean;
  worker: boolean;
  companion: boolean;
}

export default function ServiceStatusAlerts() {
  const [status, setStatus] = useState<ServiceStatus>({
    backend: true,
    db: true,
    worker: true,
    companion: true,
  });

  useEffect(() => {
    const checkServices = async () => {
      const newStatus = { ...status };

      // 1. Check Backend & Infrastructure (DB, Worker)
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        
        const res = await fetch('http://localhost:8000/health', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          const data: HealthStatus = await res.json();
          newStatus.backend = true;
          newStatus.db = data.components.db === 'connected';
          newStatus.worker = data.components.worker === 'active';
        } else {
          // Backend responded but with error code
          newStatus.backend = false;
          // Assume others are unknown/down if backend is erroring
          newStatus.db = false; 
          newStatus.worker = false;
        }
      } catch (error) {
        // Network error / timeout -> Backend unreachable
        newStatus.backend = false;
        // Cannot know status of others
        newStatus.db = false; 
        newStatus.worker = false;
      }

      // 2. Check Companion App
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);
        
        const res = await fetch('http://localhost:12345/status', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        newStatus.companion = res.ok;
      } catch (error) {
        newStatus.companion = false;
      }

      setStatus(newStatus);
    };

    // Check immediately
    checkServices();

    // Poll every 5 seconds
    const interval = setInterval(checkServices, 5000);

    return () => clearInterval(interval);
  }, []);

  // Helper to render an alert bubble
  const renderAlert = (message: string, subMessage?: string) => (
    <div className="mb-2 last:mb-0 w-full max-w-sm bg-red-50 border-l-4 border-red-500 p-4 shadow-lg rounded-r-md animate-pulse">
      <div className="flex items-center">
        <div className="flex-shrink-0">
          <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
          </svg>
        </div>
        <div className="ml-3">
          <p className="text-sm text-red-700">
            <span className="font-medium">{message}</span>
            {subMessage && (
              <>
                <br />
                {subMessage}
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );

  // If everything is fine, render nothing
  if (status.backend && status.db && status.worker && status.companion) {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end">
      {!status.backend && renderAlert("Server Unreachable", "Cannot connect to Nojoin Backend API.")}
      
      {/* Only show DB/Worker errors if backend is UP, otherwise it's redundant/unknown */}
      {status.backend && !status.db && renderAlert("Database Error", "Connection to PostgreSQL failed.")}
      {status.backend && !status.worker && renderAlert("Worker Offline", "Background processing is paused.")}
      
      {!status.companion && renderAlert("Companion App Disconnected", "Start the app to record audio.")}
    </div>
  );
}
