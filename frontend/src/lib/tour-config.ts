import { DriveStep } from "driver.js";

import type { ActivePanel, SideTab } from "./store";

/**
 * The recording page's panels the transcript tour opens to describe them. On
 * a phone each is a tab; on desktop Speakers, My notes, Captures and Meeting
 * Edge are the side column's tabs, and the page opens them there.
 */
export type TourPanel = ActivePanel;
const TOUR_PANELS: readonly TourPanel[] = [
  "transcript", "notes", "documents", "analytics", "speakers", "my-notes", "captures", "meeting-edge",
];
export const isTourPanel = (value: unknown): value is TourPanel =>
  (TOUR_PANELS as readonly unknown[]).includes(value);
const SIDE_TABS: readonly SideTab[] = ["speakers", "my-notes", "captures", "meeting-edge"];
export const isSideTab = (panel: TourPanel): panel is SideTab =>
  (SIDE_TABS as readonly TourPanel[]).includes(panel);

/** Asks the recording page to open one of its panels for a step. */
export const TOUR_SWITCH_PANEL_EVENT = "tour:switch-panel";
/**
 * Sent however the transcript tour ends, so the page puts back the panels the
 * tour found. Left on the tour's last panel, every recording opened afterwards
 * would open on it too, a new capture included (#834).
 */
export const TOUR_RESTORE_PANEL_EVENT = "tour:restore-panel";

const showPanel = (panel: TourPanel) => () => {
  window.dispatchEvent(new CustomEvent(TOUR_SWITCH_PANEL_EVENT, { detail: panel }));
};

/**
 * A side panel's selector: its tab panel in the desktop side column, or its
 * tab panel among a phone's tabs. Only one of them is ever on the page.
 */
const sidePanel = (tab: Exclude<SideTab, "speakers">) => `#side-panel-${tab}, #panel-${tab}`;
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

// A step whose element is not on the page when the tour starts is left out:
// the side column collapsed, Meeting Edge turned off, or Chat on a phone.
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
    element: '#meeting-notes',
    popover: {
      title: 'AI Notes',
      description: 'Generate and edit AI-powered meeting notes, summaries, and action items.',
      side: 'right',
    },
    onHighlightStarted: showPanel('notes'),
  },
  {
    element: '#panel-documents',
    popover: {
      title: 'Documents',
      description: 'Upload PDF or text files relevant to this meeting. Nojoin will index them so you can ask questions about them in the chat.',
      side: 'right',
    },
    onHighlightStarted: showPanel('documents'),
  },
  {
    element: '#panel-analytics',
    popover: {
      title: 'Analytics',
      description: 'Who spoke and for how long, how the conversation moved between people, and where they talked over each other, all measured from the recording.',
      side: 'right',
    },
    onHighlightStarted: showPanel('analytics'),
  },
  {
    element: '#speaker-panel',
    popover: {
      title: 'Speakers',
      description: 'Manage speakers here. You can rename them, merge them, or assign voiceprints for future recognition.',
      side: 'left',
    },
    onHighlightStarted: showPanel('speakers'),
  },
  {
    element: sidePanel('my-notes'),
    popover: {
      title: 'My Notes',
      description: 'Every note you wrote during or after the meeting, in transcript order. Select one to find its place in the transcript, or use its pencil to edit it.',
      side: 'left',
    },
    onHighlightStarted: showPanel('my-notes'),
  },
  {
    element: sidePanel('captures'),
    popover: {
      title: 'Captures',
      description: 'Stills of the shared screen and files added to your notes. Open one to view it or to choose whether the AI notes read it.',
      side: 'left',
    },
    onHighlightStarted: showPanel('captures'),
  },
  {
    element: sidePanel('meeting-edge'),
    popover: {
      title: 'Meeting Edge',
      description: 'Live guidance while you record: questions to ask, points to raise and explanations of technical terms. What it suggested stays here afterwards.',
      side: 'left',
    },
    onHighlightStarted: showPanel('meeting-edge'),
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
