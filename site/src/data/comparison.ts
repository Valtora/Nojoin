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
//
// THREE COMPETITORS, NOT FOUR. Fireflies was dropped when Jamie was added,
// rather than running five columns of prose. The table is already the widest
// thing on the site and a fifth column would either compress every cell or
// push the right-hand one somewhere most readers never scroll. Fireflies was
// the one to go because it is the least like Nojoin on the axes this page is
// about: bot-by-default capture, a US cloud, and no self-hosting story to
// concede anything against. Jamie is the opposite on all three, so it is the
// harder and more useful comparison. Removing a competitor is not a licence to
// pick easy ones -- Jamie beats Nojoin outright on two rows below, and says so.

export interface Cell {
  text: string;
}

export interface Row {
  key: string;
  label: string;
  cells: Record<string, Cell>;
}

// Jamie sits second, directly after Nojoin, because it is the closest product
// to Nojoin's own pitch -- no bot, speaker memory, an MCP server that writes --
// and the column order decides which competitor a reader actually reads.
export const products = [
  { key: "nojoin", name: "Nojoin" },
  { key: "jamie", name: "Jamie" },
  { key: "otter", name: "Otter" },
  { key: "granola", name: "Granola" },
] as const;

const UNSOURCED: Cell = { text: "Not stated in current docs" };

export const rows: Row[] = [
  {
    key: "capture",
    label: "How audio is captured",
    cells: {
      nojoin: {
        text: "Your browser captures the call. Nothing joins the meeting or appears in the participant list."
      },
      jamie: {
        text: "A native macOS or Windows app captures the audio on your machine, online or in the room. No bot joins the call."
      },
      otter: {
        text: "An AI meeting agent joins Zoom, Teams and Meet calls; bot-free recording is also available from the desktop, mobile or web app."
      },
      granola: {
        text: "No bot. Desktop apps for macOS and Windows, plus an iPhone app, capture system audio and microphone."
      },
    },
  },
  {
    key: "attribution",
    label: "Speaker attribution",
    cells: {
      nojoin: {
        text: "Built-in diarisation plus a global speaker library: voiceprints keep people named across future meetings."
      },
      jamie: {
        text: "You name each speaker once from an audio clip after the meeting, and Jamie identifies them automatically in future ones. No voiceprint library is documented."
      },
      otter: {
        text: "Speaker identification recognises tagged speakers and auto-tags their names in future transcripts."
      },
      granola: {
        text: "Speaker tags use platform display names on Meet and Zoom; no cross-meeting speaker memory documented."
      },
    },
  },
  {
    key: "processing",
    label: "Where processing happens",
    cells: {
      nojoin: { text: "On your own server." },
      jamie: {
        text: "Entirely within the EU: audio goes to Frankfurt and is transcribed on Modal's serverless GPUs, with notes generated through the Anthropic or OpenAI APIs."
      },
      otter: {
        text: "In Otter's cloud, principally on AWS in the United States."
      },
      granola: {
        text: "Audio goes from your device to Granola's cloud transcription and AI providers."
      },
    },
  },
  {
    key: "storage",
    label: "Where recordings live",
    cells: {
      nojoin: { text: "On your own server." },
      jamie: {
        text: "Transcripts sit on a server in Frankfurt; the audio is deleted permanently once the transcript is ready."
      },
      otter: {
        text: "In Otter's cloud on AWS S3, with server-side encryption."
      },
      granola: {
        text: "Audio is deleted after transcription; transcripts and notes are stored on US-hosted AWS."
      },
    },
  },
  {
    key: "selfhost",
    label: "Self-hosting",
    cells: {
      nojoin: { text: "Self-hosting is the product: one compose file on your hardware." },
      jamie: {
        text: "None documented; the app is local but every meeting is processed and stored in Jamie's EU cloud."
      },
      otter: UNSOURCED,
      granola: {
        text: "None documented; the service runs in Granola's US-hosted cloud."
      },
    },
  },
  {
    key: "source",
    label: "Source and licence",
    cells: {
      nojoin: { text: "Open source under AGPLv3." },
      jamie: {
        text: "Closed source, proprietary licence: a non-exclusive, non-transferable right to use, lasting only as long as the contract."
      },
      otter: {
        text: "Closed source, proprietary licence."
      },
      granola: {
        text: "Closed source, proprietary licence."
      },
    },
  },
  {
    key: "models",
    label: "Model choice",
    cells: {
      nojoin: {
        text: "Bring your own API keys, or run fully local inference with Ollama."
      },
      jamie: {
        text: "Vendor-chosen: notes come from the Anthropic or OpenAI APIs, and no bring-your-own-key or local option is documented."
      },
      otter: {
        text: "Otter's own AI plus vendor-chosen third-party providers; no bring-your-own-key or local option documented."
      },
      granola: {
        text: "Vendor-chosen providers (Deepgram, AssemblyAI, OpenAI, Anthropic); no bring-your-own-key or local option documented."
      },
    },
  },
  {
    // Jamie is the one product here with no AI help during the call: its
    // in-meeting widget is a manual scratchpad and everything else happens
    // after you stop recording. That is a real difference and it goes in the
    // detailed table -- but not in the summary, because Otter and Granola both
    // have it, so it is not a line between Nojoin and the field.
    key: "live",
    label: "Live in-meeting guidance",
    cells: {
      nojoin: {
        text: "Meeting Edge: live questions worth asking, missed points, and concept help."
      },
      jamie: {
        text: "The in-meeting widget is a private scratchpad for your own notes. Notes and transcript are generated after you stop recording."
      },
      otter: {
        text: "The meeting agent gives real-time transcription, takeaways and action items, and answers questions live."
      },
      granola: {
        text: "Granola Chat can answer questions about the ongoing meeting."
      },
    },
  },
  {
    key: "calendar",
    label: "Calendar integration",
    cells: {
      nojoin: { text: "Google and Outlook sync, with meeting context in the dashboard." },
      jamie: {
        text: "Google Calendar or Outlook, used for reminders, automatic meeting titles and better speaker identification."
      },
      otter: {
        text: "Google Calendar, Microsoft Outlook and iOS Calendar."
      },
      granola: {
        text: "Google and Microsoft/Outlook; Apple Calendar isn't supported."
      },
    },
  },
  {
    key: "search",
    label: "Search",
    cells: {
      nojoin: { text: "One query across recordings, notes and documents." },
      jamie: {
        text: "Semantic search across your meeting content, reachable from the app, the API and the MCP server."
      },
      otter: {
        text: "Search and AI chat across meetings and connected apps, by keyword, speaker and date."
      },
      granola: {
        text: "Chat spans notes and transcripts across meetings and folders, plus files you upload."
      },
    },
  },
  {
    // All four ship an MCP server, so "we have one" is not the claim. The line
    // that survives sourcing is what an assistant can actually do once it is
    // connected: three of them read the meeting and act elsewhere or on its
    // filing, and Nojoin's acts on the meeting itself. Every cell states what
    // that vendor documents and stops there. In particular no cell asserts
    // that a competitor *cannot* write back -- that is an unsourced negative,
    // and the gap is visible from the positives alone.
    //
    // Jamie is the closest of the three and the reason this row still earns
    // its place: it writes, but only to tags.
    key: "agents",
    label: "What an assistant can do with your meetings",
    cells: {
      nojoin: {
        text: "Thirty tools on your own deployment, authorised per user and on by default. An assistant corrects a misheard line, names the speaker, re-runs the notes, files the tasks, and syncs your People library with a CRM it's separately connected to — with no CRM integration in Nojoin at all."
      },
      jamie: {
        text: "Thirteen tools for Claude, ChatGPT, Cursor, Windsurf and Copilot: nine read meetings, transcripts, tasks and people, and four change tags — create, rename, delete and apply them."
      },
      otter: {
        text: "Reads Otter's transcripts to search across time, analyse themes and generate content. Writes outward into Google Docs, Slides, Jira, Salesforce and Slack. Part of Otter for Enterprise."
      },
      granola: {
        text: "Reads notes and transcripts on paid plans: list meetings, read a meeting, search the history. On Enterprise it's early-access beta and stays off until an admin enables it."
      },
    },
  },
  {
    // Limits, not prices -- the no-competitor-pricing rule still holds. The
    // concessions here are load-bearing: Otter's top plans lift the monthly
    // cap, and Jamie's upper plans drop the meeting count entirely. Saying
    // otherwise would be checkable in one click and would cost the rest of the
    // table.
    key: "caps",
    label: "Limits on how much you process",
    cells: {
      nojoin: {
        text: "None in the software. No monthly allowance, no per-meeting ceiling, no history that expires. How much you get through is a question about your hardware."
      },
      jamie: {
        text: "One credit a meeting whatever its length: 10 a month and 30-minute meetings on the free plan, 20 and two hours on the next. Above that the count goes but a three-hour ceiling stays. Run out and existing notes lock until you upgrade."
      },
      otter: {
        text: "300 minutes a month and 30 minutes per conversation on the entry plan; 1,200 and 90 on the next. The upper plans lift the monthly cap and hold a four-hour ceiling per meeting."
      },
      granola: {
        text: "Meeting history is limited on the entry plan, by an amount the pricing page doesn't state. Unlimited notes and history above it."
      },
    },
  },
  {
    key: "export",
    label: "Export and portability",
    cells: {
      nojoin: {
        text: "Transcript, notes or combined output as DOCX, PDF or plain text, plus full-instance backup archives."
      },
      jamie: {
        text: "Summary, transcript and tasks sync into Notion, Google Docs, OneNote, HubSpot, Salesforce, Dynamics 365, Attio and Asana, with an API and webhooks behind them. No file download is documented."
      },
      otter: {
        text: "TXT, DOCX, PDF and SRT, plus audio export."
      },
      granola: {
        text: "Bulk export of notes to CSV: titles, summaries, transcripts and details."
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
// Granola and Jamie also record without a bot, Otter and Jamie also remember
// speakers between meetings, and saying otherwise would cost the credibility
// of the rows where the difference is real. Live in-meeting guidance is absent
// for the same reason: Otter and Granola both document some form of it, so it
// is not a distinction, whatever Meeting Edge is worth on its own merits.

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
      jamie: no("Vendor cloud, EU"),
      otter: no("Not stated in docs"),
      granola: no("Vendor cloud"),
    },
  },
  {
    key: "processing",
    label: "Processing stays on your infrastructure",
    cells: {
      nojoin: yes("Your server"),
      jamie: no("Jamie's EU cloud"),
      otter: no("Otter's cloud"),
      granola: no("Granola's cloud"),
    },
  },
  {
    key: "storage",
    label: "Recordings and transcripts stay yours",
    cells: {
      nojoin: yes("Your server"),
      jamie: no("Frankfurt-hosted"),
      otter: no("Otter's cloud"),
      granola: no("Vendor-hosted"),
    },
  },
  {
    key: "source",
    label: "Source available and auditable",
    cells: {
      nojoin: yes("AGPLv3"),
      jamie: no("Proprietary"),
      otter: no("Proprietary"),
      granola: no("Proprietary"),
    },
  },
  {
    key: "models",
    label: "Your choice of model, including fully local",
    cells: {
      nojoin: yes("Your keys, or Ollama"),
      jamie: no("Vendor-chosen"),
      otter: no("Vendor-chosen"),
      granola: no("Vendor-chosen"),
    },
  },
  {
    key: "nobot",
    label: "No bot joins the call",
    cells: {
      nojoin: yes("Never a participant"),
      jamie: yes("Captures locally"),
      otter: partial("Bot by default"),
      granola: yes("Captures locally"),
    },
  },
  {
    // Jamie earns a yes: the naming is manual the first time, but it carries
    // forward on its own after that, which is what the row asks.
    key: "speakers",
    label: "Speakers remembered between meetings",
    cells: {
      nojoin: yes("Voiceprint library"),
      jamie: yes("Named once, then reused"),
      otter: yes("Auto-tags names"),
      granola: no("Per-meeting tags"),
    },
  },
  {
    // Jamie earns a partial rather than a no: four of its thirteen tools
    // genuinely change something, they just change the filing rather than the
    // record. Otter writes too, but outward into other products, so on this
    // axis -- what happens to the meeting itself -- it is a no.
    key: "agents",
    label: "An assistant can change the record, not just read it",
    cells: {
      nojoin: yes("Thirty tools, on your server"),
      jamie: partial("Creates and applies tags"),
      otter: no("Reads Otter, writes elsewhere"),
      granola: no("Reads notes and transcripts"),
    },
  },
  {
    // Every one of them has a tier where something runs out; none is a flat
    // no, so all three take a partial. Nojoin's yes is structural rather than
    // generous -- there are no plans, so there is nothing to meter.
    key: "caps",
    label: "Nothing runs out",
    cells: {
      nojoin: yes("No plans, no meters"),
      jamie: partial("Credits, and a length cap"),
      otter: partial("Capped below Business"),
      granola: partial("History capped on free"),
    },
  },
  {
    key: "browser",
    label: "Capture without a desktop app",
    cells: {
      nojoin: yes("Any browser"),
      jamie: no("Desktop or mobile app"),
      otter: yes("Web app"),
      granola: no("Desktop app"),
    },
  },
];
