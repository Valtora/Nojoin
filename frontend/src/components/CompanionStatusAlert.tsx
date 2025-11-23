"use client";

import { useState, useEffect } from 'react';

export default function CompanionStatusAlert() {
  const [isConnected, setIsConnected] = useState<boolean>(true);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);
        
        const res = await fetch('http://localhost:12345/status', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          setIsConnected(true);
        } else {
          setIsConnected(false);
        }
      } catch (error) {
        setIsConnected(false);
      }
    };

    // Check immediately
    checkStatus();

    // Then poll every 5 seconds
    const interval = setInterval(checkStatus, 5000);

    return () => clearInterval(interval);
  }, []);

  if (isConnected) {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-sm bg-red-50 border-l-4 border-red-500 p-4 shadow-lg rounded-r-md animate-pulse">
      <div className="flex items-center">
        <div className="flex-shrink-0">
          <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
          </svg>
        </div>
        <div className="ml-3">
          <p className="text-sm text-red-700">
            <span className="font-medium">Companion App Disconnected</span>
            <br />
            Please start the Nojoin Companion App to record audio.
          </p>
        </div>
      </div>
    </div>
  );
}
