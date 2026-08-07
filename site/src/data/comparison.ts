// The comparison data. Rules, from the plan:
// - Every competitor claim is verified against that vendor's own current
//   documentation before it goes up. The page no longer displays the URL or the
//   date read, but the standard behind the cell has not changed. A claim that
//   cannot be checked does not go on the page: it appears as the explicit
//   "Not stated in current docs" cell.
// - No competitor pricing, ever. Limits and caps are fine.
// - Nojoin's claims trace to README.md or a file in docs/.
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
    // This row carries a truth the summary table compresses. "No bot joins the
    // call" is one summary row, so Otter's bot-free desktop, mobile and web
    // recording has nowhere else to appear. It appears here, in full.
    key: "capture",
    label: "How audio is captured",
    cells: {
      nojoin: {
        text: "Your browser captures the call. Nothing joins the meeting or appears in the participant list."
      },
      jamie: {
        text: "A native macOS or Windows app captures audio on your machine, online or in the room. No bot joins the call."
      },
      otter: {
        text: "An AI agent joins Zoom, Teams and Meet calls. Bot-free recording is also available from the desktop, mobile or web app."
      },
      granola: {
        text: "No bot. Desktop apps for macOS and Windows, plus an iPhone app, capture system audio and microphone."
      },
    },
  },
  {
    // Two summary rows come from this one detailed row, because there are two
    // questions in it and they have different answers. Whether speakers are
    // remembered: Jamie and Otter both do it, and both say so here. Whether you
    // hold the voice models: that is Nojoin's, and it is argued from USAGE.md
    // (a library you can open, recalibration from better samples, automatic
    // rebuilds after an upgrade, deletion that removes the voiceprint) rather
    // than from any competitor being worse than they are. No cell below claims
    // a competitor cannot match speakers between meetings, because all three
    // vendors document that they can.
    key: "attribution",
    label: "Speaker attribution",
    cells: {
      nojoin: {
        text: "A speaker library you can open. Voiceprints recalibrate from better samples and rebuild after an upgrade."
      },
      jamie: {
        text: "Name each speaker once from an audio clip; Jamie identifies them in later meetings. No voiceprint library documented."
      },
      otter: {
        text: "Speaker identification recognises tagged speakers and auto-tags them in later transcripts."
      },
      granola: {
        text: "Speaker tags come from platform display names on Meet and Zoom. No cross-meeting memory documented."
      },
    },
  },
  {
    key: "processing",
    label: "Where processing happens",
    cells: {
      nojoin: { text: "On your own server." },
      jamie: {
        text: "Entirely in the EU: audio to Frankfurt, transcribed on Modal's GPUs, notes through the Anthropic or OpenAI APIs."
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
        text: "Transcripts sit on a server in Frankfurt; audio is deleted permanently once the transcript is ready."
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
        text: "Closed source: a non-transferable right to use, lasting as long as the contract."
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
        text: "Vendor-chosen: notes come from the Anthropic or OpenAI APIs. No bring-your-own-key or local option documented."
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
        text: "The in-meeting widget is a scratchpad for your own notes. Notes and transcript come after you stop recording."
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
        text: "Google Calendar or Outlook, for reminders, meeting titles and better speaker identification."
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
        text: "Semantic search across your meeting content, from the app, the API and the MCP server."
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
    // All four ship an MCP server, so "we have one" is not the claim, and no
    // row on this page should imply otherwise. The line that survives sourcing
    // is what an assistant can actually do once it is connected: three of them
    // read the meeting and act elsewhere or on its filing, and Nojoin's acts on
    // the meeting itself. Every cell states what that vendor documents and
    // stops there. In particular no cell asserts that a competitor *cannot*
    // write back -- that is an unsourced negative, and the gap is visible from
    // the positives alone.
    //
    // Jamie is the closest of the three and the reason this row still earns
    // its place: it writes, but only to tags.
    key: "agents",
    label: "What an assistant can do with your meetings",
    cells: {
      nojoin: {
        text: "Thirty tools on your deployment: correct a line, name a speaker, re-run the notes, file the tasks. Every change is labelled and reversible."
      },
      jamie: {
        text: "Thirteen tools for Claude, ChatGPT, Cursor and Copilot. Nine read meetings, transcripts, tasks and people; four create and apply tags."
      },
      otter: {
        text: "Reads transcripts to search, analyse themes and generate content. Writes outward into Docs, Jira, Salesforce and Slack. Otter for Enterprise."
      },
      granola: {
        text: "Reads notes and transcripts on paid plans: list, read and search meetings. On Enterprise it's beta, off until an admin enables it."
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
        text: "None. No allowance, no per-meeting ceiling, no history that expires. What you get through is a hardware question."
      },
      jamie: {
        text: "One credit a meeting. 10 a month and 30-minute meetings free; 20 and two hours next. Higher plans drop the count, keep a three-hour cap."
      },
      otter: {
        text: "300 minutes a month, 30 per conversation, on the entry plan; 1,200 and 90 next. Upper plans lift the monthly cap but keep a four-hour ceiling."
      },
      granola: {
        text: "Meeting history is limited on the entry plan, by an amount the pricing page doesn't state. Unlimited above it."
      },
    },
  },
  {
    // Otter takes an outright yes in the summary off the back of this row: it
    // exports more file formats than Nojoin does, SRT among them. That stands.
    // Nojoin's answer to SRT is that every transcript export is already
    // timestamped line by line (USAGE.md, "Transcript And Playback"), so the
    // cell states what the export contains rather than disputing the format.
    key: "export",
    label: "Export and portability",
    cells: {
      nojoin: {
        text: "Transcript, notes or both as TXT, PDF or DOCX, timestamped per line, plus MP3 audio and full-instance backups."
      },
      jamie: {
        text: "Summary, transcript and tasks sync into Notion, Google Docs, HubSpot, Salesforce, Asana and more, with an API and webhooks. No file download documented."
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

// The at-a-glance summary. Every verdict here is the short form of one or more
// rows in `rows` above, so the claim and its sourcing live in one place.
//
// WHY THIS TABLE IS EIGHT ROWS AND NOT TEN. It used to run five separate rows
// for one argument -- self-hosting, processing, storage, licence, model choice
// -- which all read Nojoin yes and three crosses. That inflated the win count
// without adding an idea, and the site's own rule is to say each idea once.
// They are now two rows. The rows recovered went to axes that discriminate:
// who holds the voice models, and whether your data comes out whole.
//
// Nojoin does not sweep this table on the competitors' side of it, and that is
// deliberate. Granola and Jamie also record without a bot. Otter and Jamie also
// remember speakers between meetings, which is why the speaker question is two
// rows: the first concedes it plainly, and the second asks the question only
// the second one answers differently. Otter exports more file formats than
// Nojoin does. Every one of those is a tie or a competitor win, and removing
// them would cost the credibility of the rows where the difference is real.
//
// Live in-meeting guidance stays out for the same reason it always did: Otter
// and Granola both document some form of it, so it is not a line between Nojoin
// and the field, whatever Meeting Edge is worth on its own merits.

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
    // Collapsed from three rows (self-hosting, processing, storage) that gave
    // the same answer three times. Merging them also resolves Otter's
    // "not stated in docs" self-hosting cell: where its processing and storage
    // run is documented, so the merged row can say so.
    key: "ownership",
    label: "Runs on your hardware, and stays there",
    cells: {
      nojoin: yes("One compose file"),
      jamie: no("Jamie's EU cloud"),
      otter: no("Otter's US cloud"),
      granola: no("Granola's US cloud"),
    },
  },
  {
    // Collapsed from two rows. The licence and the model are one question to a
    // buyer: how much of this stack is yours to change.
    key: "open",
    label: "Open source, and your choice of model",
    cells: {
      nojoin: yes("AGPLv3, your keys or Ollama"),
      jamie: no("Proprietary, vendor-chosen"),
      otter: no("Proprietary, vendor-chosen"),
      granola: no("Proprietary, vendor-chosen"),
    },
  },
  {
    // Merged with the old "capture without a desktop app" row. Nojoin's note
    // carries both halves. Jamie and Granola keep their outright yes: neither
    // sends a bot either, and pretending otherwise would be false.
    key: "nobot",
    label: "No bot joins the call",
    cells: {
      nojoin: yes("Any browser, never a participant"),
      jamie: yes("Captures locally"),
      otter: partial("Bot by default"),
      granola: yes("Captures locally"),
    },
  },
  {
    // This row concedes the point, deliberately. Jamie and Otter both
    // genuinely match speakers across meetings -- Otter's help centre describes
    // acoustic enrolment, not a name lookup. Stated plainly here, the row below
    // reads as the specific claim it is rather than as a claim about speaker
    // memory that a reader would rightly disbelieve.
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
    // The row above concedes that everyone remembers speakers. This one asks
    // what happens to the voice model afterwards, and it is argued entirely
    // from USAGE.md: a library you can open, recalibration from a better
    // sample, background rebuilds from the original audio after an upgrade,
    // and deletion that removes the voiceprint. The competitor cells say what
    // was looked for and not found. They do not assert that no such feature
    // exists, because that is an unsourced negative.
    key: "voiceprints",
    label: "Voice models you hold and can rebuild",
    cells: {
      nojoin: yes("Recalibrate, rebuild, delete"),
      jamie: no("Not stated in docs"),
      otter: no("Not stated in docs"),
      granola: no("Not stated in docs"),
    },
  },
  {
    // Jamie earns a partial rather than a no: four of its thirteen tools
    // genuinely change something, they just change the filing rather than the
    // record. Otter writes too, but outward into other products, so on this
    // axis -- what happens to the meeting itself -- it is a no.
    key: "agents",
    label: "An assistant can change the record",
    cells: {
      nojoin: yes("Thirty tools, your server"),
      jamie: partial("Creates and applies tags"),
      otter: no("Reads Otter, writes elsewhere"),
      granola: no("Reads notes and transcripts"),
    },
  },
  {
    // Otter takes an outright yes here and keeps it. It exports more file
    // formats than Nojoin does. Jamie's partial is not a slight: it syncs into
    // eight products and documents no file download at all, which is a
    // different shape of portability rather than a lesser amount of it.
    key: "portability",
    label: "Your data comes out whole",
    cells: {
      nojoin: yes("Documents, audio, backups"),
      jamie: partial("Syncs to apps, no download"),
      otter: yes("Documents and audio"),
      granola: partial("CSV of notes"),
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
];
