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
const MCP = src("https://github.com/Valtora/Nojoin/blob/main/docs/MCP.md");

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
        text: "Google and Microsoft/Outlook; Apple Calendar isn't supported.",
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
    // All four ship an MCP server, so "we have one" is not the claim. The line
    // that survives sourcing is what an assistant can actually do once it is
    // connected: three of them read the meeting and act elsewhere, and Nojoin's
    // acts on the meeting itself. Every cell states what that vendor documents
    // and stops there. In particular no cell asserts that a competitor *cannot*
    // write back -- that is an unsourced negative, and the gap is visible from
    // the positives alone.
    key: "agents",
    label: "What an assistant can do with your meetings",
    cells: {
      nojoin: {
        text: "Thirty tools on your own deployment, authorised per user and on by default. An assistant corrects a misheard line, names the speaker, re-runs the notes, files the tasks, and syncs your People library with a CRM it's separately connected to — with no CRM integration in Nojoin at all.",
        source: MCP,
      },
      otter: {
        text: "Reads Otter's transcripts to search across time, analyse themes and generate content. Writes outward into Google Docs, Slides, Jira, Salesforce and Slack. Part of Otter for Enterprise.",
        source: src("https://otter.ai/blog/otter-mcp-your-meetings-now-power-every-tool-you-use"),
      },
      granola: {
        text: "Reads notes and transcripts on paid plans: list meetings, read a meeting, search the history. On Enterprise it's early-access beta and stays off until an admin enables it.",
        source: src("https://www.granola.ai/blog/granola-mcp"),
      },
      fireflies: {
        text: "Nineteen tools: 14 read, and 5 change something about the filing — rename a meeting, move it to a channel, share it, revoke that share, cut a soundbite.",
        source: src("https://docs.fireflies.ai/mcp-tools/overview"),
      },
    },
  },
  {
    // Limits, not prices -- the no-competitor-pricing rule still holds. The
    // concessions here are load-bearing: Fireflies transcribes without limit on
    // every plan, and Otter's top plans lift the monthly cap. Saying otherwise
    // would be checkable in one click and would cost the rest of the table.
    key: "caps",
    label: "Limits on how much you process",
    cells: {
      nojoin: {
        text: "None in the software. No monthly allowance, no per-meeting ceiling, no history that expires. How much you get through is a question about your hardware.",
        source: README,
      },
      otter: {
        text: "300 minutes a month and 30 minutes per conversation on the entry plan; 1,200 and 90 on the next. The upper plans lift the monthly cap and hold a four-hour ceiling per meeting.",
        source: src("https://otter.ai/pricing"),
      },
      granola: {
        text: "Meeting history is limited on the entry plan, by an amount the pricing page doesn't state. Unlimited notes and history above it.",
        source: src("https://www.granola.ai/pricing"),
      },
      fireflies: {
        text: "Transcription is unlimited on every plan. Storage isn't: 400 minutes a team on the entry plan, 8,000 a seat above it. AI features draw on a monthly credit allowance.",
        source: src("https://fireflies.ai/pricing"),
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

// The at-a-glance summary. Every verdict here is the short form of a row in
// `rows` above, so the sourcing lives in one place: the detailed table carries
// the URL and checked date for each claim, and this table says so rather than
// repeating footnotes that could drift out of step with it.
//
// The rows were chosen because their answers are structural -- where the
// software runs, who holds the recordings, what the licence permits -- rather
// than roadmap-sensitive. Nojoin does not sweep the table, deliberately:
// Granola also records without a bot, Otter also remembers speakers between
// meetings, and saying otherwise would cost the credibility of the rows where
// the difference is real. Live in-meeting guidance is absent for the same
// reason: all four products document some form of it, so it is not a
// distinction, whatever Meeting Edge is worth on its own merits.

export type Verdict = "yes" | "partial" | "no";

export interface SummaryCell {
  verdict: Verdict;
  note: string;
}

export interface SummaryRow {
  key: string;
  label: string;
  cells: Record<string, SummaryCell>;
}

const yes = (note: string): SummaryCell => ({ verdict: "yes", note });
const partial = (note: string): SummaryCell => ({ verdict: "partial", note });
const no = (note: string): SummaryCell => ({ verdict: "no", note });

export const summaryRows: SummaryRow[] = [
  {
    key: "selfhost",
    label: "Runs on your own hardware",
    cells: {
      nojoin: yes("One compose file"),
      otter: no("Not stated in docs"),
      granola: no("Vendor cloud"),
      fireflies: no("Vendor cloud"),
    },
  },
  {
    key: "processing",
    label: "Processing stays on your infrastructure",
    cells: {
      nojoin: yes("Your server"),
      otter: no("Otter's cloud"),
      granola: no("Granola's cloud"),
      fireflies: no("Fireflies' cloud"),
    },
  },
  {
    key: "storage",
    label: "Recordings and transcripts stay yours",
    cells: {
      nojoin: yes("Your server"),
      otter: no("Otter's cloud"),
      granola: no("Vendor-hosted"),
      fireflies: partial("Own bucket on Enterprise"),
    },
  },
  {
    key: "source",
    label: "Source available and auditable",
    cells: {
      nojoin: yes("AGPLv3"),
      otter: no("Proprietary"),
      granola: no("Proprietary"),
      fireflies: no("Proprietary"),
    },
  },
  {
    key: "models",
    label: "Your choice of model, including fully local",
    cells: {
      nojoin: yes("Your keys, or Ollama"),
      otter: no("Vendor-chosen"),
      granola: no("Vendor-chosen"),
      fireflies: no("Vendor-chosen"),
    },
  },
  {
    key: "nobot",
    label: "No bot joins the call",
    cells: {
      nojoin: yes("Never a participant"),
      otter: partial("Bot by default"),
      granola: yes("Captures locally"),
      fireflies: partial("Bot by default"),
    },
  },
  {
    key: "speakers",
    label: "Speakers remembered between meetings",
    cells: {
      nojoin: yes("Voiceprint library"),
      otter: yes("Auto-tags names"),
      granola: no("Per-meeting tags"),
      fireflies: no("Per-transcript edits"),
    },
  },
  {
    // Fireflies earns a partial rather than a no: five of its tools genuinely
    // change something, they just change the filing rather than the record.
    // Otter writes too, but outward into other products, so on this axis --
    // what happens to the meeting itself -- it is a no.
    key: "agents",
    label: "An assistant can change the record, not just read it",
    cells: {
      nojoin: yes("Thirty tools, on your server"),
      otter: no("Reads Otter, writes elsewhere"),
      granola: no("Reads notes and transcripts"),
      fireflies: partial("Renames, moves, shares"),
    },
  },
  {
    // Every one of them has a tier where something runs out; none of them is a
    // flat no, so all three take a partial. Nojoin's yes is structural rather
    // than generous -- there are no plans, so there is nothing to meter.
    key: "caps",
    label: "Nothing runs out",
    cells: {
      nojoin: yes("No plans, no meters"),
      otter: partial("Capped below Business"),
      granola: partial("History capped on free"),
      fireflies: partial("Storage and AI credits"),
    },
  },
  {
    key: "browser",
    label: "Capture without a desktop app",
    cells: {
      nojoin: yes("Any browser"),
      otter: yes("Web app"),
      granola: no("Desktop app"),
      fireflies: partial("Chrome extension"),
    },
  },
];
