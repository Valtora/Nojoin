import { create } from 'zustand';

interface HealthStatus {
  status: string;
  version: string;
  components: {
    db: string;
    worker: string;
  };
}

interface AudioLevels {
  input_level: number;
  output_level: number;
  is_recording: boolean;
}

interface ServiceStatusState {
  backend: boolean;
  db: boolean;
  worker: boolean;
  companion: boolean;
  
  // Companion details
  companionStatus: 'idle' | 'recording' | 'paused' | 'error';
  recordingDuration: number;
  
  // Audio levels
  audioLevels: {
    input: number;
    output: number;
  };
  
  // Polling state
  isPolling: boolean;
  backendFailCount: number;
  companionFailCount: number;
  
  // Actions
  checkBackend: () => Promise<void>;
  checkCompanion: () => Promise<void>;
  checkAudioLevels: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 32000, 60000];
const NORMAL_INTERVAL = 10000;

export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let backendTimer: NodeJS.Timeout | null = null;
  let companionTimer: NodeJS.Timeout | null = null;
  let audioTimer: NodeJS.Timeout | null = null;

  const scheduleNextBackend = () => {
    if (!get().isPolling) return;
    
    const failCount = get().backendFailCount;
    const delay = failCount === 0 
      ? NORMAL_INTERVAL 
      : BACKOFF_DELAYS[Math.min(failCount - 1, BACKOFF_DELAYS.length - 1)];
      
    if (backendTimer) clearTimeout(backendTimer);
    backendTimer = setTimeout(() => {
      get().checkBackend();
    }, delay);
  };

  const scheduleNextCompanion = () => {
    if (!get().isPolling) return;
    
    const failCount = get().companionFailCount;
    const delay = failCount === 0 
      ? NORMAL_INTERVAL 
      : BACKOFF_DELAYS[Math.min(failCount - 1, BACKOFF_DELAYS.length - 1)];
      
    if (companionTimer) clearTimeout(companionTimer);
    companionTimer = setTimeout(() => {
      get().checkCompanion();
    }, delay);
  };

  // Audio levels need frequent updates when recording, but we can pause if companion is down
  const scheduleNextAudio = () => {
    if (!get().isPolling) return;
    
    // Only poll audio if companion is up and recording (optimization)
    // But we need to know if it's recording first.
    // For now, let's keep audio check separate or tied to companion check?
    // The original code checked audio every 2s.
    // If we reduce health check to 10s, audio meters will be very laggy.
    // Maybe audio check should only run when we know we are recording?
    // Or maybe we keep audio check frequent (2s) but only if companion is reachable?
    
    // User said: "Change the health check poll to only occur every 10 seconds"
    // Audio levels are technically different, but "Check all parts... ensure they are not doing so excessively".
    // Polling audio levels every 2s when idle is excessive.
    
    const { companion, companionStatus } = get();
    let delay = 2000;
    
    if (!companion) {
      delay = 10000; // If companion down, check less often
    } else if (companionStatus !== 'recording') {
      delay = 5000; // If not recording, check less often (just to detect start)
    } else {
      delay = 1000; // If recording, check often for UI feedback
    }
    
    if (audioTimer) clearTimeout(audioTimer);
    audioTimer = setTimeout(() => {
      get().checkAudioLevels();
    }, delay);
  };

  return {
    backend: true,
    db: true,
    worker: true,
    companion: true,
    companionStatus: 'idle',
    recordingDuration: 0,
    audioLevels: { input: 0, output: 0 },
    isPolling: false,
    backendFailCount: 0,
    companionFailCount: 0,

    checkBackend: async () => {
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
          set({ 
            backend: true, 
            db: data.components.db === 'connected', 
            worker: data.components.worker === 'active',
            backendFailCount: 0 
          });
        } else {
          set(state => ({ 
            backend: false, 
            db: false, 
            worker: false,
            backendFailCount: state.backendFailCount + 1 
          }));
        }
      } catch {
        set(state => ({ 
          backend: false, 
          db: false, 
          worker: false,
          backendFailCount: state.backendFailCount + 1 
        }));
      }
      scheduleNextBackend();
    },

    checkCompanion: async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);
        
        const res = await fetch('http://localhost:12345/status', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          const data = await res.json();
          let status: 'idle' | 'recording' | 'paused' | 'error' = 'idle';
          let duration = 0;

          if (typeof data === 'object' && data.status) {
             let s = '';
             if (typeof data.status === 'string') {
                 s = data.status.toLowerCase();
             } else if (typeof data.status === 'object') {
                 s = Object.keys(data.status)[0].toLowerCase();
             }

             if (s === 'idle') status = 'idle';
             else if (s === 'recording') status = 'recording';
             else if (s === 'paused') status = 'paused';
             
             if (typeof data.duration_seconds === 'number') {
                 duration = data.duration_seconds;
             }
          } else if (typeof data === 'string') {
            const s = data.toLowerCase();
            if (s === 'idle') status = 'idle';
            else if (s === 'recording') status = 'recording';
            else if (s === 'paused') status = 'paused';
          }

          set({ 
            companion: true, 
            companionStatus: status,
            recordingDuration: duration,
            companionFailCount: 0 
          });
        } else {
          set(state => ({ 
            companion: false, 
            companionFailCount: state.companionFailCount + 1 
          }));
        }
      } catch {
        set(state => ({ 
          companion: false, 
          companionFailCount: state.companionFailCount + 1 
        }));
      }
      scheduleNextCompanion();
    },

    checkAudioLevels: async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);
        
        const res = await fetch('http://localhost:12345/levels', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          const data: AudioLevels = await res.json();
          set({ 
            audioLevels: { 
              input: data.input_level, 
              output: data.output_level 
            } 
          });
        }
      } catch {
        // Ignore audio level errors, handled by companion check
      }
      scheduleNextAudio();
    },

    startPolling: () => {
      if (get().isPolling) return;
      set({ isPolling: true });
      get().checkBackend();
      get().checkCompanion();
      get().checkAudioLevels();
    },

    stopPolling: () => {
      set({ isPolling: false });
      if (backendTimer) clearTimeout(backendTimer);
      if (companionTimer) clearTimeout(companionTimer);
      if (audioTimer) clearTimeout(audioTimer);
    }
  };
});
