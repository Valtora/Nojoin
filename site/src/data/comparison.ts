// The comparison data. Rules, from the plan:
// - Every competitor claim is verified against that vendor's own current
//   documentation, carries the exact URL read and the date it was checked,
//   and renders as a footnote. A claim that cannot be sourced does not go on
//   the page: it appears as the explicit "Not stated in current docs" cell.
// - No competitor pricing, ever.
// - Nojoin's claims cite the repository documentation the same way.
//
// All vendor pages below were fetched and read on the checked date, and each
// URL was re-verified to return 200 before this file was committed.

export interface Cell {
  text: string;
  source?: { url: string; checked: string };
}

export interface Row {
  key: string;
  label: string;
  cells: Record<string, Cell>;
}

export const products = [
  { key: "nojoin", name: "Nojoin" },
  { key: "otter", name: "Otter" },
  { key: "granola", name: "Granola" },
  { key: "fireflies", name: "Fireflies" },
] as const;

const CHECKED = "2 Aug 2026";
const src = (url: string) => ({ url, checked: CHECKED });

const README = src("https://github.com/Valtora/Nojoin/blob/main/README.md");
const USAGE = src("https://github.com/Valtora/Nojoin/blob/main/docs/USAGE.md");

const UNSOURCED: Cell = { text: "Not stated in current docs" };

export const rows: Row[] = [
  {
    key: "capture",
    label: "How audio is captured",
    cells: {
      nojoin: {
        text: "Your browser captures the call. Nothing joins the meeting or appears in the participant list.",
        source: README,
      },
      otter: {
        text: "An AI meeting agent joins Zoom, Teams and Meet calls; bot-free recording is also available from the desktop, mobile or web app.",
        source: src("https://otter.ai/"),
      },
      granola: {
        text: "No bot. Desktop apps for macOS and Windows, plus an iPhone app, capture system audio and microphone.",
        source: src("https://docs.granola.ai/help-center/taking-notes/transcription"),
      },
      fireflies: {
        text: "The Fireflies Notetaker bot joins the call; bot-free capture exists via the Chrome extension on Meet and the mobile app.",
        source: src("https://guide.fireflies.ai/articles/9554534786-how-fireflies-joins-and-records-your-meetings-faqs"),
      },
    },
  },
  {
    key: "attribution",
    label: "Speaker attribution",
    cells: {
      nojoin: {
        text: "Built-in diarisation plus a global speaker library: voiceprints keep people named across future meetings.",
        source: README,
      },
      otter: {
        text: "Speaker identification recognises tagged speakers and auto-tags their names in future transcripts.",
        source: src("https://otter.ai/privacy-policy"),
      },
      granola: {
        text: "Speaker tags use platform display names on Meet and Zoom; no cross-meeting speaker memory documented.",
        source: src("https://docs.granola.ai/help-center/taking-notes/transcription"),
      },
      fireflies: {
        text: "Automatic labels, generic without platform metadata; edits apply to the current transcript, with no documented cross-meeting memory.",
        source: src("https://guide.fireflies.ai/articles/4994477228-how-to-edit-speaker-labels-or-names-in-a-transcript"),
      },
    },
  },
  {
    key: "processing",
    label: "Where processing happens",
    cells: {
      nojoin: { text: "On your own server.", source: README },
      otter: {
        text: "In Otter's cloud, principally on AWS in the United States.",
        source: src("https://otter.ai/privacy-policy"),
      },
      granola: {
        text: "Audio goes from your device to Granola's cloud transcription and AI providers.",
        source: src("https://docs.granola.ai/help-center/taking-notes/transcription"),
      },
      fireflies: {
        text: "In Fireflies' US cloud on AWS and GCP, including for EU-stored data.",
        source: src("https://guide.fireflies.ai/articles/9596505232-learn-about-data-storage-and-transfer"),
      },
    },
  },
  {
    key: "storage",
    label: "Where recordings live",
    cells: {
      nojoin: { text: "On your own server.", source: README },
      otter: {
        text: "In Otter's cloud on AWS S3, with server-side encryption.",
        source: src("https://otter.ai/privacy-security"),
      },
      granola: {
        text: "Audio is deleted after transcription; transcripts and notes are stored on US-hosted AWS.",
        source: src("https://docs.granola.ai/help-center/consent-security-privacy/security-privacy-data-faqs"),
      },
      fireflies: {
        text: "Fireflies' US cloud by default; Enterprise can bring their own S3 or GCS bucket.",
        source: src("https://guide.fireflies.ai/articles/9596505232-learn-about-data-storage-and-transfer"),
      },
    },
  },
  {
    key: "selfhost",
    label: "Self-hosting",
    cells: {
      nojoin: { text: "Self-hosting is the product: one compose file on your hardware.", source: README },
      otter: UNSOURCED,
      granola: {
        text: "None documented; the service runs in Granola's US-hosted cloud.",
        source: src("https://www.granola.ai/security"),
      },
      fireflies: {
        text: "None documented; Enterprise private storage still leaves the service Fireflies-hosted.",
        source: src("https://guide.fireflies.ai/articles/9596505232-learn-about-data-storage-and-transfer"),
      },
    },
  },
  {
    key: "source",
    label: "Source and licence",
    cells: {
      nojoin: { text: "Open source under AGPLv3.", source: README },
      otter: {
        text: "Closed source, proprietary licence.",
        source: src("https://otter.ai/terms-of-service"),
      },
      granola: {
        text: "Closed source, proprietary licence.",
        source: src("https://docs.granola.ai/help-center/policies/terms-of-service/application-terms-of-service"),
      },
      fireflies: {
        text: "Closed source, proprietary licence.",
        source: src("https://fireflies.ai/terms-of-service"),
      },
    },
  },
  {
    key: "models",
    label: "Model choice",
    cells: {
      nojoin: {
        text: "Bring your own API keys, or run fully local inference with Ollama.",
        source: README,
      },
      otter: {
        text: "Otter's own AI plus vendor-chosen third-party providers; no bring-your-own-key or local option documented.",
        source: src("https://otter.ai/privacy-policy"),
      },
      granola: {
        text: "Vendor-chosen providers (Deepgram, AssemblyAI, OpenAI, Anthropic); no bring-your-own-key or local option documented.",
        source: src("https://www.granola.ai/security"),
      },
      fireflies: {
        text: "Vendor-chosen partners including OpenAI and Anthropic; no bring-your-own-key or local option documented.",
        source: src("https://guide.fireflies.ai/articles/2154538358-policy-on-keeping-information-safe"),
      },
    },
  },
  {
    key: "live",
    label: "Live in-meeting guidance",
    cells: {
      nojoin: {
        text: "Meeting Edge: live questions worth asking, missed points, and concept help.",
        source: README,
      },
      otter: {
        text: "The meeting agent gives real-time transcription, takeaways and action items, and answers questions live.",
        source: src("https://otter.ai/"),
      },
      granola: {
        text: "Granola Chat can answer questions about the ongoing meeting.",
        source: src("https://docs.granola.ai/help-center/getting-more-from-your-notes/chatting-with-your-meetings"),
      },
      fireflies: {
        text: "Live Assist: real-time transcripts, notes, suggestions and AskFred answers during the meeting.",
        source: src("https://guide.fireflies.ai/articles/6032274417-learn-about-fireflies-live-assist-get-real-time-suggestions-answers-and-notes-live-during-the-meeting"),
      },
    },
  },
  {
    key: "calendar",
    label: "Calendar integration",
    cells: {
      nojoin: { text: "Google and Outlook sync, with meeting context in the dashboard.", source: README },
      otter: {
        text: "Google Calendar, Microsoft Outlook and iOS Calendar.",
        source: src("https://otter.ai/features"),
      },
      granola: {
        text: "Google and Microsoft/Outlook; Apple Calendar is not supported.",
        source: src("https://docs.granola.ai/help-center/getting-started/syncing-your-calendars"),
      },
      fireflies: {
        text: "Google Calendar or Outlook, one connected calendar per account.",
        source: src("https://guide.fireflies.ai/articles/4246295295-what-calendars-are-supported"),
      },
    },
  },
  {
    key: "search",
    label: "Search",
    cells: {
      nojoin: { text: "One query across recordings, notes and documents.", source: README },
      otter: {
        text: "Search and AI chat across meetings and connected apps, by keyword, speaker and date.",
        source: src("https://otter.ai/"),
      },
      granola: {
        text: "Chat spans notes and transcripts across meetings and folders, plus files you upload.",
        source: src("https://docs.granola.ai/help-center/getting-more-from-your-notes/chatting-with-your-meetings"),
      },
      fireflies: {
        text: "Across recordings and transcripts, with host, participant, title and date filters.",
        source: src("https://guide.fireflies.ai/articles/4577578901-how-to-search-and-find-your-meetings"),
      },
    },
  },
  {
    key: "export",
    label: "Export and portability",
    cells: {
      nojoin: {
        text: "Transcript, notes or combined output as DOCX, PDF or plain text, plus full-instance backup archives.",
        source: USAGE,
      },
      otter: {
        text: "TXT, DOCX, PDF and SRT, plus audio export.",
        source: src("https://otter.ai/transcription"),
      },
      granola: {
        text: "Bulk export of notes to CSV: titles, summaries, transcripts and details.",
        source: src("https://docs.granola.ai/help-center/sharing/exporting-notes"),
      },
      fireflies: {
        text: "Transcripts in PDF, DOCX, SRT, CSV, JSON or MD; summaries, video and audio too.",
        source: src("https://guide.fireflies.ai/articles/3319752033-how-to-download-transcripts-summaries-and-meeting-recordings-from-fireflies"),
      },
    },
  },
];
