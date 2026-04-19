import { DriveStep } from "driver.js";

export const dashboardSteps: DriveStep[] = [
  {
    element: '#nav-dashboard',
    popover: {
      title: 'Welcome to Nojoin',
      description: 'This is your home base. Dashboard is where Nojoin gives you the quickest overview of what matters right now.',
      side: 'right',
      align: 'start',
    }
  },
  {
    element: '#dashboard-upcoming-meetings',
    popover: {
      title: 'Upcoming Meetings',
      description: 'Keep track of what is coming up next and what needs your attention before the meeting starts.',
      side: 'bottom',
    }
  },
  {
    element: '#dashboard-meeting-controls',
    popover: {
      title: 'Meeting Controls',
      description: 'Start, pause, and manage live capture from here once your Companion App is connected.',
      side: 'left',
    }
  },
  {
    element: '#dashboard-task-cards',
    popover: {
      title: 'Task Cards',
      description: 'Capture follow-ups and deadlines here so action items stay visible between meetings.',
      side: 'left',
    }
  },
  {
    element: '#dashboard-recent-meetings',
    popover: {
      title: 'Recent Meetings',
      description: 'Your latest processed meetings appear here. When you are ready to explore transcripts, head into Recordings next.',
      side: 'left',
    }
  },
  {
    element: '#nav-recordings',
    popover: {
      title: 'Recordings Are Next',
      description: 'Open Recordings when you want to inspect saved meetings, open transcripts, and work through prior sessions.',
      side: 'right',
    }
  },
];

export const recordingsSteps: DriveStep[] = [
  {
    element: '#nav-recordings',
    popover: {
      title: 'Recordings Workspace',
      description: 'This area is for reviewing processed meetings, opening transcripts, and working through stored audio sessions.',
      side: 'right',
    }
  },
  {
    element: '#recordings-landing-panel',
    popover: {
      title: 'Start Here',
      description: 'When this workspace is empty, use the Start Meeting button to record a session or go to the Help page in Settings if you need guidance.',
      side: 'left',
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
