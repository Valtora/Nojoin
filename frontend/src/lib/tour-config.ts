import { DriveStep } from "driver.js";

export const dashboardSteps: DriveStep[] = [
  {
    element: '#main-nav',
    popover: {
      title: 'Welcome to Nojoin',
      description: 'This is your main navigation bar. From here you can access your recordings, settings, and more.',
      side: 'right',
      align: 'start',
    }
  },
  {
    element: '#nav-recordings',
    popover: {
      title: 'Recordings',
      description: 'View all your processed meetings and audio files here.',
      side: 'right',
    }
  },
  {
    element: '#sidebar-recordings-list',
    popover: {
      title: 'Recordings List',
      description: 'Your recordings will appear here. You can search, filter, and select them to view details.',
      side: 'right',
    }
  },
  {
    element: '#nav-people',
    popover: {
      title: 'People & Speakers',
      description: 'Manage your Global Speaker Library here. Identify speakers, merge duplicates, and manage voiceprints across all your meetings.',
      side: 'right',
    }
  },
  {
    element: '#demo-recording-card',
    popover: {
      title: 'Demo Recording',
      description: 'We have created a sample recording for you. Click on it to explore the transcript view features.',
      side: 'right',
    }
  },
  {
    element: '#nav-settings',
    popover: {
      title: 'Settings',
      description: 'Configure your AI models, storage paths, and other preferences here.',
      side: 'right',
    }
  },
  {
    element: '#nav-import',
    popover: {
      title: 'Import Audio',
      description: 'Have existing recordings? Import them here to process them with Nojoin.',
      side: 'right',
    }
  },
  {
    element: '#nav-download-companion',
    popover: {
      title: 'Download Companion App',
      description: 'To start recording meetings, please download the Companion App. It runs in the background and captures audio securely.',
      side: 'top',
    }
  },
  {
    element: '#nav-connect-companion',
    popover: {
      title: 'Connect Companion App',
      description: 'Your Companion App is running! Click here to connect it to Nojoin and start recording.',
      side: 'top',
    }
  }
];

export const transcriptSteps: DriveStep[] = [
  {
    element: '#transcript-view',
    popover: {
      title: 'Transcript View',
      description: 'This is where you can read the full transcript of your meeting. Click on any segment to jump to that point in the audio.',
      side: 'right',
    },
    onHighlightStarted: () => {
      window.dispatchEvent(new CustomEvent('tour:switch-panel', { detail: 'transcript' }));
    }
  },
  {
    element: '#audio-player',
    popover: {
      title: 'Audio Controls',
      description: 'Play, pause, and scrub through the recording. You can also adjust playback speed.',
      side: 'top',
    }
  },
  {
    element: '#speaker-panel',
    popover: {
      title: 'Speakers',
      description: 'Manage speakers here. You can rename them, merge them, or assign voiceprints for future recognition.',
      side: 'left',
    }
  },
  {
    element: '#meeting-notes',
    popover: {
      title: 'AI Notes',
      description: 'Generate and edit AI-powered meeting notes, summaries, and action items.',
      side: 'right',
    },
    onHighlightStarted: () => {
      window.dispatchEvent(new CustomEvent('tour:switch-panel', { detail: 'notes' }));
    }
  },
  {
    element: '#tab-documents',
    popover: {
      title: 'Documents',
      description: 'Upload PDF or text files relevant to this meeting. Nojoin will index them so you can ask questions about them in the chat.',
      side: 'right',
    },
    onHighlightStarted: () => {
      window.dispatchEvent(new CustomEvent('tour:switch-panel', { detail: 'documents' }));
    }
  },
  {
    element: '#meeting-chat',
    popover: {
      title: 'Meeting Chat',
      description: 'Ask questions about the meeting using the AI chat assistant. Use the chat to get answers from the transcript and uploaded documents.',
      side: 'left',
    }
  },
  {
    element: '#chat-context-toggle',
    popover: {
      title: 'Cross-Meeting Context',
      description: 'Enable context from other meetings by selecting tags. This lets the AI answer questions using information from multiple related recordings.',
      side: 'left',
    }
  }
];
