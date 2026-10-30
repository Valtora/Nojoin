import { DriveStep } from "driver.js";

import type { ActivePanel } from "./store";

/** The recording page's panels the transcript tour opens to describe them. */
export type TourPanel = Extract<ActivePanel, "transcript" | "notes" | "documents">;
const TOUR_PANELS: readonly TourPanel[] = ["transcript", "notes", "documents"];
export const isTourPanel = (value: unknown): value is TourPanel =>
  (TOUR_PANELS as readonly unknown[]).includes(value);

/** Asks the recording page to open one of its panels for a step. */
export const TOUR_SWITCH_PANEL_EVENT = "tour:switch-panel";
/**
 * Sent however the transcript tour ends, so the page puts back the panel the
 * tour found. Left on the tour's last panel, every recording opened afterwards
 * would open on it too, a new capture included (#834).
 */
export const TOUR_RESTORE_PANEL_EVENT = "tour:restore-panel";

const showPanel = (panel: TourPanel) => () => {
  window.dispatchEvent(new CustomEvent(TOUR_SWITCH_PANEL_EVENT, { detail: panel }));
};
export const restoreTourPanel = () => {
  window.dispatchEvent(new CustomEvent(TOUR_RESTORE_PANEL_EVENT));
};

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
      description: 'The Meet Now card is where live capture starts. Click Start Meeting when ready, or resolve the paused-recording or browser-support message before trying again.',
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
    element: '#dashboard-upcoming-meetings',
    popover: {
      title: 'Meeting History',
      description: 'The calendar also surfaces recorded meetings in orange, including tags and speakers, so past sessions stay visible alongside calendar events.',
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
      description: 'When this workspace is empty, go to the Meet Now card on the dashboard and click Start Meeting to record a session, or use Import Audio at the top of the recordings list to add an existing file. If a button is disabled, follow the paused-recording or browser-support guidance first.',
      side: 'left',
    }
  }
];

export const transcriptSteps: DriveStep[] = [
  {
    element: '#transcript-view',
    popover: {
      title: 'Transcript View',
      description: 'This is where you read the full transcript of your meeting. Select some text to add a note on it, or use the ⋯ button beside a speaker\'s name to play from that line, edit its text or change its speaker.',
      side: 'right',
    },
    onHighlightStarted: showPanel('transcript'),
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
    onHighlightStarted: showPanel('notes'),
  },
  {
    element: '#tab-documents',
    popover: {
      title: 'Documents',
      description: 'Upload PDF or text files relevant to this meeting. Nojoin will index them so you can ask questions about them in the chat.',
      side: 'right',
    },
    onHighlightStarted: showPanel('documents'),
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
