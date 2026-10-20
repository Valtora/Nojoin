import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  TimelineNote,
  TimelineNoteCreate,
  TimelineNoteErrorCode,
  TimelineNoteUpdate,
} from "@/types";

import HeadNotePanel from "./HeadNotePanel";

const RECORDING = "rec-1";

// The code points Python's str.isspace() accepts: what the API's strip()
// trims from a note's body.
const PYTHON_SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

const pythonStrip = (text: string) => {
  let start = 0;
  let end = text.length;
  while (start < end && PYTHON_SPACE.has(text.charCodeAt(start))) start += 1;
  while (end > start && PYTHON_SPACE.has(text.charCodeAt(end - 1))) end -= 1;
  return text.slice(start, end);
};

const refusal = (status: number, detail: unknown) => ({
  isAxiosError: true,
  response: { status, data: { detail } },
});

/** A refusal as the notes routes send it: a stable code and some text. */
const coded = (
  status: number,
  code: TimelineNoteErrorCode,
  extra: Record<string, unknown> = {},
) => refusal(status, { code, message: `Refused (${code})`, ...extra });

/** The write token another device's writes carry. */
const OTHER_DEVICE = "device-b-token";

type Write = "create" | "update" | "remove";

/**
 * The real panel and the real store against a fake of the notes routes that
 * behaves like them: notes scoped to the recording, the caller as author,
 * per-note revisions and write tokens, 409 `revision_conflict` with the
 * current note on a stale edit or delete, a tombstone for every delete so a
 * write to a deleted note gets 410 `note_deleted_by_you` (its token made the
 * delete) or `note_deleted_elsewhere`, 404 `note_not_found` for a note that
 * never was (except a delete with a token, which leaves a tombstone so a late
 * create of the id is refused), 422 for an empty or too long body after Python's strip, a
 * retried create returning the note as it is now, an edit that changes
 * nothing keeping its revision and token only when it carries no token,
 * writes that commit but lose their response, and requests refused before
 * they run.
 */
class FakeServer {
  notes: TimelineNote[] = [];
  /** Deleted note ids and the token each delete carried. */
  readonly tombstones = new Map<string, string | null>();
  private clock = 0;
  /** The next write of each kind commits, but its response is lost. */
  readonly lose = new Set<Write>();
  /** The next write of each kind commits, and is answered with this error. */
  readonly answerWith = new Map<Write, unknown>();
  /** The next create's new id turns out to be held on another recording. */
  nextIdTaken = false;
  /** Writes wait for this before they run. */
  gate: Promise<void> | null = null;
  /** Refusals for the next writes, in order, each before its write runs. */
  readonly refuse: { write: Write; error: unknown }[] = [];
  /** How many of the next lists fail without an answer. */
  failLists = 0;

  /** A note as another device (or, with this device's token, this one) made it. */
  add(
    id: string,
    kind: TimelineNote["kind"],
    body: string,
    author = "dana",
    token: string | null = OTHER_DEVICE,
  ) {
    this.clock += 1;
    const at = new Date(Date.UTC(2026, 9, 17, 10, 0, this.clock)).toISOString();
    const note: TimelineNote = {
      id,
      recording_id: RECORDING,
      kind,
      body,
      revision: 1,
      last_write_token: token,
      created_by: { id: author === "dana" ? 1 : 2, username: author },
      created_at: at,
      updated_at: at,
    };
    this.notes.push(note);
    this.notes.sort((a, b) => a.created_at.localeCompare(b.created_at));
    return note;
  }

  /** Another device's edit. */
  edit(id: string, body: string) {
    const note = this.notes.find((candidate) => candidate.id === id)!;
    note.body = body;
    note.revision += 1;
    note.last_write_token = OTHER_DEVICE;
  }

  /** Another device's delete. */
  drop(id: string) {
    this.bury(id, OTHER_DEVICE);
  }

  list = () => this.notes.map((note) => ({ ...note }));

  listFor = async (recordingId: string) => {
    this.scope(recordingId);
    if (this.failLists > 0) {
      this.failLists -= 1;
      throw new Error("Network Error");
    }
    return this.list();
  };

  create = async (recordingId: string, payload: TimelineNoteCreate) => {
    this.scope(recordingId);
    this.refused("create");
    const body = this.clean(payload.body);
    if (this.gate) await this.gate;
    const existing = this.notes.find((note) => note.id === payload.id);
    if (existing) return { ...existing };
    if (this.tombstones.has(payload.id!))
      throw this.deleted(payload.id!, payload.write_token);
    if (this.nextIdTaken) {
      this.nextIdTaken = false;
      throw coded(409, "note_id_in_use");
    }
    const created = this.add(
      payload.id!,
      payload.kind,
      body,
      "dana",
      payload.write_token ?? null,
    );
    this.respond("create");
    return { ...created };
  };

  update = async (
    recordingId: string,
    id: string,
    update: TimelineNoteUpdate,
  ) => {
    this.scope(recordingId);
    this.refused("update");
    const body = update.body == null ? null : this.clean(update.body);
    if (this.gate) await this.gate;
    const note = this.find(id, update.write_token);
    if (note.revision !== update.expected_revision) {
      throw coded(409, "revision_conflict", { note: { ...note } });
    }
    // A repeat without a token keeps its revision; with one, it makes one, so
    // a write of the same revision still in transit is refused when it lands.
    const repeat = body === null || body === note.body;
    if (repeat && update.write_token === undefined) return { ...note };
    if (body !== null) note.body = body;
    note.revision += 1;
    note.last_write_token = update.write_token ?? null;
    this.respond("update");
    return { ...note };
  };

  remove = async (
    recordingId: string,
    id: string,
    conditions: { expectedRevision?: number; writeToken?: string } = {},
  ) => {
    this.scope(recordingId);
    this.refused("remove");
    const { expectedRevision, writeToken } = conditions;
    if (
      writeToken !== undefined &&
      this.tombstones.get(id) === writeToken &&
      !this.notes.some((note) => note.id === id)
    ) {
      return; // A retry of the delete that made the tombstone.
    }
    if (
      writeToken !== undefined &&
      !this.tombstones.has(id) &&
      !this.notes.some((note) => note.id === id)
    ) {
      // An id no note has held: recorded, so a late create of it is refused.
      this.bury(id, writeToken);
      this.respond("remove");
      return;
    }
    const note = this.find(id, writeToken);
    if (expectedRevision !== undefined && note.revision !== expectedRevision) {
      throw coded(409, "revision_conflict", { note: { ...note } });
    }
    this.bury(id, writeToken ?? null);
    this.respond("remove");
  };

  private bury(id: string, token: string | null) {
    this.notes = this.notes.filter((candidate) => candidate.id !== id);
    this.tombstones.set(id, token);
  }

  private scope(recordingId: string) {
    if (recordingId !== RECORDING) throw coded(404, "recording_not_found");
  }

  private deleted(id: string, token: string | undefined) {
    return coded(
      410,
      token !== undefined && this.tombstones.get(id) === token
        ? "note_deleted_by_you"
        : "note_deleted_elsewhere",
    );
  }

  private find(id: string, token: string | undefined) {
    const note = this.notes.find((candidate) => candidate.id === id);
    if (note) return note;
    if (this.tombstones.has(id)) throw this.deleted(id, token);
    throw coded(404, "note_not_found");
  }

  private clean(body: string) {
    const cleaned = pythonStrip(body);
    if (!cleaned || [...cleaned].length > 50_000) {
      throw refusal(422, [{ msg: "body must not be empty or too long" }]);
    }
    return cleaned;
  }

  private refused(write: Write) {
    if (this.refuse[0]?.write !== write) return;
    throw this.refuse.shift()!.error;
  }

  private respond(write: Write) {
    if (this.lose.delete(write)) throw new Error("Network Error");
    if (this.answerWith.has(write)) {
      const error = this.answerWith.get(write);
      this.answerWith.delete(write);
      throw error;
    }
  }
}

let server: FakeServer;
const calls = {
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
};

vi.mock("@/lib/api", () => ({
  listTimelineNotes: (recordingId: string, signal?: AbortSignal) => {
    calls.list(recordingId, signal);
    return server.listFor(recordingId);
  },
  createTimelineNote: (...args: [string, TimelineNoteCreate]) => {
    calls.create(...args);
    return server.create(...args);
  },
  updateTimelineNote: (...args: [string, string, TimelineNoteUpdate]) => {
    calls.update(...args);
    return server.update(...args);
  },
  deleteTimelineNote: (
    ...args: [
      string,
      string,
      { expectedRevision?: number; writeToken?: string }?,
    ]
  ) => {
    calls.remove(...args);
    return server.remove(...args);
  },
}));

const signature = (notes: TimelineNote[]) =>
  notes.map((note) => `${note.id}:${note.revision}`).join(",");

const settle = () => vi.advanceTimersByTimeAsync(0);

const mount = (props: { disabled?: boolean } = {}) => {
  let shown = server.list();
  let disabled = props.disabled;
  const element = () => (
    <HeadNotePanel
      recordingId={RECORDING}
      timelineNotes={shown}
      disabled={disabled}
    />
  );
  const view = render(element());
  // Like useRecordingDetail, the page takes a polled recording only when its
  // notes signature (or something else it shows) changed.
  const answer = (notes: TimelineNote[]) =>
    act(async () => {
      if (signature(notes) !== signature(shown)) shown = notes;
      view.rerender(element());
      await settle();
    });
  return {
    poll: () => answer(server.list()),
    /** A poll answering with these notes, such as a late one. */
    answer,
    lock: (locked: boolean) =>
      act(async () => {
        disabled = locked;
        view.rerender(element());
        await settle();
      }),
    unmount: () =>
      act(async () => {
        view.unmount();
        await settle();
      }),
  };
};

const box = () => screen.getByRole("textbox") as HTMLTextAreaElement;
const type = (text: string) =>
  fireEvent.change(box(), { target: { value: text } });
const waitForSave = () => act(() => vi.advanceTimersByTimeAsync(1800));
const blur = () =>
  act(async () => {
    fireEvent.blur(box());
    await settle();
  });
const click = (name: string) =>
  act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
    await settle();
  });
const bodies = () => server.notes.map((note) => note.body);
// The save state as shown; the status region repeats some of it for screen readers.
const SHOWN = { ignore: "script, style, [role=status]" };

describe("HeadNotePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    server = new FakeServer();
    for (const call of Object.values(calls)) call.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("refuses an edit made while another device's change arrived, and shows theirs", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();

    type("Original, mine");
    // Device A saves before this box's debounce fires, and a poll brings it in.
    server.edit("h1", "Theirs");
    await poll();
    expect(box().value).toBe("Original, mine");

    await waitForSave();
    expect(calls.update).toHaveBeenLastCalledWith(
      RECORDING,
      "h1",
      expect.objectContaining({ expected_revision: 1, body: "Original, mine" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Theirs");
    expect(screen.getByText("Changed elsewhere", SHOWN)).toBeInTheDocument();

    // Leaving the box does not save over their version unasked.
    await blur();
    expect(calls.update).toHaveBeenCalledTimes(1);

    await click("Keep mine");
    expect(calls.update).toHaveBeenLastCalledWith(
      RECORDING,
      "h1",
      expect.objectContaining({ expected_revision: 2, body: "Original, mine" }),
    );
    expect(bodies()).toEqual(["Original, mine"]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes the other version when asked", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();

    await click("Use theirs");

    expect(box().value).toBe("Theirs");
    await blur();
    expect(calls.update).toHaveBeenCalledTimes(1);
    expect(bodies()).toEqual(["Theirs"]);
  });

  it("finishes a create whose response was lost with the newer text", async () => {
    server.lose.add("create");
    mount();

    type("Agenda");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();

    type("Agenda, budget");
    await waitForSave();

    const [first, second] = calls.create.mock.calls.map(
      ([, payload]) => payload.id,
    );
    expect(second).toBe(first);
    expect(bodies()).toEqual(["Agenda, budget"]);
    expect(box().value).toBe("Agenda, budget");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not write a retried create over an edit made on another device", async () => {
    server.lose.add("create");
    const { poll } = mount();
    type("Agenda");
    await waitForSave();
    // Another device sees the note the lost create made, and edits it.
    server.edit(server.notes[0].id, "Device B: the decision");
    await poll();

    type("Agenda, budget");
    await waitForSave();

    expect(bodies()).toEqual(["Device B: the decision"]);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Device B: the decision",
    );
    expect(box().value).toBe("Agenda, budget");

    await click("Keep mine");
    expect(bodies()).toEqual(["Agenda, budget"]);
  });

  it("takes a refusal caused by its own save whose response was lost as saved", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("update");
    type("Mine");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();

    type("Mine, more");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Mine, more"]);
    expect(server.notes[0].revision).toBe(3);
  });

  it("sends one request when the box is left while the same text is saving", async () => {
    server.add("h1", "head", "Original");
    mount();
    let release!: () => void;
    server.gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    type("Mine");
    await waitForSave();
    await blur();
    server.gate = null;
    await act(async () => {
      release();
      await settle();
    });

    expect(calls.update).toHaveBeenCalledTimes(1);
    expect(server.notes[0].revision).toBe(2);
  });

  it("deletes the note a lost create made when the box is cleared", async () => {
    server.lose.add("create");
    const { poll } = mount();
    type("Secret");
    await waitForSave();
    const [{ id }] = server.notes;

    type("");
    await poll();
    expect(box().value).toBe("");

    await waitForSave();
    expect(calls.remove).toHaveBeenCalledWith(
      RECORDING,
      id,
      expect.objectContaining({ expectedRevision: 1 }),
    );
    expect(server.notes).toEqual([]);
    expect(box().value).toBe("");
  });

  it("does not list its own unconfirmed create as another note", async () => {
    server.lose.add("create");
    const { poll } = mount();
    type("Agenda");
    await waitForSave();
    type("Agenda, more");
    await poll();

    expect(
      screen.queryByRole("list", { name: "Other notes" }),
    ).not.toBeInTheDocument();
  });

  it("makes the note under a new id when its id is taken elsewhere", async () => {
    server.nextIdTaken = true;
    mount();
    type("Agenda");
    await waitForSave();

    const [first, second] = calls.create.mock.calls.map(
      ([, payload]) => payload.id,
    );
    expect(second).not.toBe(first);
    expect(server.notes.map((note) => [note.id, note.body])).toEqual([
      [second, "Agenda"],
    ]);
    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
  });

  it("keeps its own note when another device created one at the same time", async () => {
    const { poll } = mount();
    // The other device's create lands first.
    server.add("theirs", "head", "From device B", "otto");
    type("From device A");
    await waitForSave();
    await poll();

    expect(box().value).toBe("From device A");
    const other = screen.getByRole("list", { name: "Other notes" });
    expect(other).toHaveTextContent("From device B");

    type("From device A, edited");
    await waitForSave();
    expect(bodies()).toEqual(["From device B", "From device A, edited"]);
  });

  it("empties the box when a poll shows another device deleted the note", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();

    server.drop("h1");
    await poll();

    expect(calls.list).toHaveBeenCalledTimes(1);
    expect(box().value).toBe("");
    // Later polls are unchanged, so the page does not hand them on.
    await poll();
    expect(box().value).toBe("");
  });

  it("does not take a poll answered out of order for a delete", async () => {
    const { poll, answer } = mount();
    type("Agenda");
    await waitForSave();
    await poll();
    // A slower, earlier poll arrives after the fresh one.
    await answer([]);
    expect(calls.list).toHaveBeenCalledTimes(1);
    expect(box().value).toBe("Agenda");

    type("Agenda, budget");
    await waitForSave();
    expect(calls.create).toHaveBeenCalledTimes(1);
    expect(bodies()).toEqual(["Agenda, budget"]);
  });

  it("deletes a note that arrived by poll when the box is cleared", async () => {
    const { poll } = mount();
    server.add("h1", "head", "From device B", "otto");
    await poll();
    expect(box().value).toBe("From device B");

    type("");
    await waitForSave();

    expect(calls.remove).toHaveBeenCalledWith(
      RECORDING,
      "h1",
      expect.objectContaining({ expectedRevision: 1 }),
    );
    expect(server.notes).toEqual([]);
  });

  it("refuses to clear notes another device changed meanwhile", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("");
    server.edit("h1", "Theirs, important");
    await poll();
    await waitForSave();

    expect(bodies()).toEqual(["Theirs, important"]);
    expect(screen.getByRole("alert")).toHaveTextContent("Theirs, important");

    await click("Delete anyway");
    expect(server.notes).toEqual([]);
  });

  it("keeps mine even when mine is the text it last saved", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();
    // Back to what the box last saved, then insist on it.
    type("Original");
    await click("Keep mine");

    expect(bodies()).toEqual(["Original"]);
    expect(box().value).toBe("Original");
  });

  it("does not keep mine while notes generation holds the notes", async () => {
    server.add("h1", "head", "Original");
    const { poll, lock } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();
    await lock(true);

    const keepMine = screen.getByRole("button", { name: "Keep mine" });
    expect(keepMine).toBeDisabled();
    await act(async () => {
      fireEvent.click(keepMine);
      await settle();
    });
    expect(calls.update).toHaveBeenCalledTimes(1);
    expect(bodies()).toEqual(["Theirs"]);
  });

  it("saves nothing when only outer whitespace changed, as the server trims it", async () => {
    const { poll } = mount();
    type("Item one\n");
    await waitForSave();
    await poll();
    type("\u0085Item one\u001c");
    await waitForSave();

    expect(calls.create).toHaveBeenCalledTimes(1);
    expect(calls.update).not.toHaveBeenCalled();
    // The box knows it holds the saved note, so it starts no save at all.
    expect(screen.queryByText("Saved", SHOWN)).not.toBeInTheDocument();
  });

  it("asks before re-creating a note deleted under an edit", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.drop("h1");
    await poll();
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(calls.create).not.toHaveBeenCalled();

    await click("Keep mine");
    expect(bodies()).toEqual(["Mine"]);
  });

  it("leaves a trailing new line in the box when the saved note comes back trimmed", async () => {
    const { poll } = mount();
    type("Item one\n");
    await waitForSave();
    await poll();

    expect(bodies()).toEqual(["Item one"]);
    expect(box().value).toBe("Item one\n");
  });

  it("deletes the note when the box is cleared, and a late poll cannot restore it", async () => {
    server.add("h1", "head", "Original");
    const stale = server.list();
    const { poll, answer } = mount();

    type("");
    await waitForSave();
    expect(server.notes).toEqual([]);
    await poll();

    await answer(stale);
    expect(box().value).toBe("");
  });

  it("saves pending text when the box goes away", async () => {
    const { unmount } = mount();
    type("Before leaving");
    await unmount();

    expect(bodies()).toEqual(["Before leaving"]);
  });

  it("schedules nothing once the box is gone", async () => {
    const { unmount } = mount();
    let release!: () => void;
    server.gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    type("First");
    await waitForSave();
    type("First, then more");
    await unmount();
    server.gate = null;
    await act(async () => {
      release();
      await settle();
    });

    expect(vi.getTimerCount()).toBe(0);
    expect(bodies()).toEqual(["First, then more"]);
  });

  it("lists appended notes read-only", () => {
    server.add("h1", "head", "Agenda");
    server.add("t1", "tail", "Sent the recap");
    mount();

    const other = screen.getByRole("list", { name: "Other notes" });
    expect(other).toHaveTextContent("Sent the recap");
    expect(box().value).toBe("Agenda");
  });

  it("counts characters near the limit, typed or from the server", async () => {
    server.add("h1", "head", "Short");
    const { poll } = mount();
    expect(screen.queryByText(/characters/)).not.toBeInTheDocument();

    server.edit("h1", "y".repeat(48_000));
    await poll();
    expect(screen.getByText(/48,000 \/ 50,000 characters/)).toBeInTheDocument();

    type("x".repeat(49_000));
    expect(screen.getByText(/49,000 \/ 50,000 characters/)).toBeInTheDocument();
  });

  it("counts the box's own text while a change from the server waits", async () => {
    server.add("h1", "head", "Short");
    const { poll } = mount();
    type("x".repeat(46_000));
    server.edit("h1", "y".repeat(48_000));
    await poll();

    expect(screen.getByText(/46,000 \/ 50,000 characters/)).toBeInTheDocument();
  });

  it("counts characters as the server does, in code points", () => {
    mount();
    type("\u{1F600}".repeat(46_000));
    expect(screen.getByText(/46,000 \/ 50,000 characters/)).toBeInTheDocument();
  });

  it("keeps text over the limit in the box and saves none of it", async () => {
    mount();
    type("x".repeat(50_001));
    await waitForSave();

    expect(box().value).toHaveLength(50_001);
    expect(
      screen.getByText(/Shorten the notes to save them/),
    ).toBeInTheDocument();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("asks before re-creating a note its lost create made and another device deleted", async () => {
    server.lose.add("create");
    const { poll } = mount();
    type("Agenda");
    await waitForSave();
    const [{ id }] = server.notes;
    // A poll here shows the note; another device then deletes it.
    await poll();
    server.drop(id);
    await poll();

    type("Agenda, budget");
    await waitForSave();

    expect(server.notes).toEqual([]);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    // The retry was told the note was deleted elsewhere, and made nothing.
    expect(calls.create).toHaveBeenCalledTimes(2);

    await click("Keep mine");
    expect(bodies()).toEqual(["Agenda, budget"]);
    expect(server.notes[0].id).not.toBe(id);
  });

  it("asks again when the check for a note missing from a poll fails", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    server.drop("h1");
    server.failLists = 1;
    await poll();
    expect(box().value).toBe("Original");

    // Later polls are unchanged, so the page hands none of them on.
    await poll();
    await act(() => vi.advanceTimersByTimeAsync(2_000));

    expect(calls.list).toHaveBeenCalledTimes(2);
    expect(box().value).toBe("");
  });

  it("does not take a delete refused for a missing recording as done", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.refuse.push({
      write: "remove",
      error: coded(404, "recording_not_found"),
    });
    type("");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // The box still edits that note, rather than making a second one.
    type("Original, kept");
    await waitForSave();
    expect(bodies()).toEqual(["Original, kept"]);
  });

  it("keeps a create that arrives after the box was cleared from landing", async () => {
    const { poll } = mount();
    server.refuse.push({
      write: "create",
      error: refusal(504, "Gateway Timeout"),
    });
    type("Secret");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    const [[, lostCreate]] = calls.create.mock.calls;

    type("");
    await waitForSave();
    // The proxy gave up on the create, but it reaches the backend now.
    await expect(server.create(RECORDING, lostCreate)).rejects.toMatchObject({
      response: { status: 410 },
    });
    await poll();

    expect(box().value).toBe("");
    expect(server.notes).toEqual([]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes its own delete whose response was lost as done when typing again", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("remove");
    type("");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();

    type("Fresh start");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Fresh start"]);
  });

  it("clears the note its own unanswered edit made", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("update");
    type("Mine");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();

    type("");
    await waitForSave();

    expect(
      calls.remove.mock.calls.map(
        ([, , conditions]) => conditions?.expectedRevision,
      ),
    ).toEqual([1, 2]);
    expect(server.notes).toEqual([]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes clearing a note already deleted elsewhere as done", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.drop("h1");
    type("");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
    expect(server.notes).toEqual([]);
  });

  it("saves again when the box is left during a save that then fails", async () => {
    server.add("h1", "head", "Original");
    mount();
    let release!: () => void;
    server.gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    type("Mine");
    await waitForSave();
    await blur();
    // The save in flight lands, but its response is lost.
    server.lose.add("update");
    server.gate = null;
    await act(async () => {
      release();
      await settle();
    });

    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
    expect(bodies()).toEqual(["Mine"]);
  });

  it("returns focus to the box once a conflict is resolved", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();

    screen.getByRole("button", { name: "Use theirs" }).focus();
    await click("Use theirs");
    expect(box()).toHaveFocus();

    type("Mine again");
    server.edit("h1", "Theirs again");
    await poll();
    await waitForSave();
    screen.getByRole("button", { name: "Keep mine" }).focus();
    await click("Keep mine");
    expect(box()).toHaveFocus();
  });

  it("shows the box's text in the notice while notes generation covers the box", async () => {
    server.add("h1", "head", "Original");
    const { poll, lock } = mount();
    type("Mine, to copy");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();
    await lock(true);

    const notice = screen.getByRole("alert");
    expect(within(notice).getByText("Mine, to copy")).toBeInTheDocument();
    expect(notice).toHaveTextContent("Theirs");

    screen.getByRole("button", { name: "Use theirs" }).focus();
    await click("Use theirs");
    expect(document.activeElement).toHaveTextContent(
      "Notes are temporarily locked",
    );
  });

  it("names the box, and announces its state and why text over the limit is not saved", async () => {
    mount();
    type("x".repeat(50_001));
    await waitForSave();

    const notes = screen.getByRole("textbox", { name: "Notes" });
    expect(notes).toHaveAttribute("aria-invalid", "true");
    expect(notes).toHaveAccessibleDescription(/Shorten the notes to save them/);
    expect(screen.getByRole("status")).toHaveTextContent("Save failed");
  });

  it("offers only Dismiss for a deleted note when the box is blank", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.drop("h1");
    await poll();
    await waitForSave();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );

    type("");
    expect(
      screen.queryByRole("button", { name: "Keep mine" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Discard mine" }),
    ).not.toBeInTheDocument();
    await click("Dismiss");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(box().value).toBe("");
    await waitForSave();
    expect(server.notes).toEqual([]);
  });

  it("shows the head note the box moves to after discarding, even with the same text", async () => {
    server.add("h1", "head", "Shared");
    server.add("h2", "head", "Shared", "otto");
    const { poll } = mount();
    type("Mine");
    server.drop("h1");
    await poll();
    await waitForSave();

    await click("Discard mine");

    expect(box().value).toBe("Shared");
    type("Shared, more");
    await waitForSave();
    expect(server.notes.map((note) => [note.id, note.body])).toEqual([
      ["h2", "Shared, more"],
    ]);
  });

  it("counts text with Windows line ends as the box will send it", () => {
    server.add("h1", "head", "abcdefg\r\n".repeat(6_000));
    mount();

    expect(screen.getByText(/47,999 \/ 50,000 characters/)).toBeInTheDocument();
    expect(screen.queryByText(/Shorten the notes/)).not.toBeInTheDocument();
  });

  it("makes a new note after its own unanswered delete of a lost create, never retrying that create", async () => {
    server.lose.add("create");
    const { poll } = mount();
    type("Agenda");
    await waitForSave();
    // A poll shows the note the lost create made, while the box is unsaved.
    await poll();
    server.lose.add("remove");
    type("");
    await waitForSave();
    expect(server.notes).toEqual([]);

    type("Fresh");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Fresh"]);
    // The cleared create's id is not sent again.
    const [lost, fresh, ...rest] = calls.create.mock.calls.map(
      ([, payload]) => payload,
    );
    expect(rest).toEqual([]);
    expect(fresh.id).not.toBe(lost.id);
  });

  it("does not bring a note back after Discard mine with no poll since its delete", async () => {
    server.add("h1", "head", "Original");
    mount();
    type("Mine");
    server.drop("h1");
    await waitForSave();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );

    await click("Discard mine");
    expect(box().value).toBe("");
    type("Next");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Next"]);
    expect(calls.update).toHaveBeenCalledTimes(1);
  });

  it("takes its own landed save as its own however many saves failed after it", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("update");
    type("a");
    await waitForSave();
    for (const text of ["ab", "abc", "abcd"]) {
      server.refuse.push({
        write: "update",
        error: new Error("Network Error"),
      });
      type(text);
      await waitForSave();
    }

    type("abcde");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["abcde"]);
  });

  it("takes another device's write of the very text it sent as a conflict", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.refuse.push({ write: "update", error: new Error("Network Error") });
    type("Same words");
    await waitForSave();
    // The other device happens to save exactly what this one tried.
    server.edit("h1", "Same words");

    type("Same words, and more");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "changed on another device",
    );
    expect(bodies()).toEqual(["Same words"]);
  });

  it("asks before re-creating a lost create's note deleted elsewhere, with no poll between", async () => {
    server.lose.add("create");
    mount();
    type("Agenda");
    await waitForSave();
    server.drop(server.notes[0].id);

    type("Agenda, budget");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(server.notes).toEqual([]);
    await click("Keep mine");
    expect(bodies()).toEqual(["Agenda, budget"]);
  });

  it("saves the old text again after its own unanswered delete, rather than taking it as saved", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("remove");
    type("");
    await waitForSave();
    expect(server.notes).toEqual([]);

    // Typed back exactly as the deleted note read.
    type("Original");
    await waitForSave();

    expect(bodies()).toEqual(["Original"]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes a delete elsewhere of a note it was clearing itself as agreed", async () => {
    server.add("h1", "head", "Original");
    mount();
    // This device's delete never reaches the server; another device's does.
    server.refuse.push({ write: "remove", error: new Error("Network Error") });
    type("");
    await waitForSave();
    server.drop("h1");

    type("A new start");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["A new start"]);
  });

  it("sends a retry under the same token and the next write under a new one", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.refuse.push({ write: "update", error: new Error("Network Error") });
    type("One");
    await waitForSave();
    type("Two");
    await waitForSave();
    type("Three");
    await waitForSave();

    const tokens = calls.update.mock.calls.map(
      ([, , update]) => update.write_token,
    );
    expect(tokens).toHaveLength(3);
    expect(tokens[1]).toBe(tokens[0]);
    expect(tokens[2]).not.toBe(tokens[1]);
    expect(server.notes[0].last_write_token).toBe(tokens[2]);
  });

  it("branches on the refusal's code, not its text", async () => {
    server.add("h1", "head", "Original");
    mount();
    // "Note not found" as text, but the code says the recording is gone.
    server.refuse.push({
      write: "update",
      error: refusal(404, {
        code: "recording_not_found",
        message: "Note not found",
      }),
    });
    type("Mine");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // And a deleted note is known by its code whatever the text says.
    server.drop("h1");
    server.refuse.push({
      write: "update",
      error: refusal(410, {
        code: "note_deleted_elsewhere",
        message: "Recording not found",
      }),
    });
    type("Mine, again");
    await waitForSave();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
  });

  it("names the conflict's kind in the save state, without announcing it twice", async () => {
    server.add("h1", "head", "Original");
    server.add("h2", "head", "Second");
    const { poll } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();
    expect(screen.getByText("Changed elsewhere", SHOWN)).toBeInTheDocument();
    // One announcement: the notice's own, as an alert.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "changed on another device",
    );
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    await click("Use theirs");
    type("Mine once more");
    server.drop("h1");
    await waitForSave();
    expect(screen.getByText("Deleted elsewhere", SHOWN)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("announces a failed save and its recovery, not every routine save", async () => {
    server.add("h1", "head", "Original");
    mount();
    type("Mine");
    await waitForSave();
    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    server.refuse.push({ write: "update", error: new Error("Network Error") });
    type("Mine, more");
    await waitForSave();
    expect(screen.getByRole("status")).toHaveTextContent("Save failed");

    type("Mine, more again");
    await waitForSave();
    expect(screen.getByRole("status")).toHaveTextContent("Saved");

    type("Mine, and on");
    await waitForSave();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("hands focus to the lock when it covers the box, and back when it ends", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    box().focus();

    await lock(true);
    const locked = screen.getByRole("group", {
      name: "Notes are temporarily locked",
    });
    expect(locked).toHaveFocus();
    expect(locked).toHaveAccessibleDescription(/being incorporated|folded/);

    await lock(false);
    expect(box()).toHaveFocus();
  });

  it("returns focus to the box after a conflict resolved during the lock, once it ends", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await waitForSave();
    await lock(true);
    await click("Use theirs");
    expect(
      screen.getByRole("group", { name: /temporarily locked/ }),
    ).toHaveFocus();

    await lock(false);
    expect(box()).toHaveFocus();
  });

  it("leaves focus alone when the lock ends after focus moved on", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    const elsewhere = document.createElement("button");
    document.body.append(elsewhere);
    box().focus();
    await lock(true);
    act(() => elsewhere.focus());

    await lock(false);
    expect(elsewhere).toHaveFocus();
    elsewhere.remove();
  });

  it("asks again after a failed check from 2 s, doubling up to 30 s", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    server.drop("h1");
    server.failLists = 100;
    await poll();
    expect(calls.list).toHaveBeenCalledTimes(1);

    const waits = [2_000, 4_000, 8_000, 16_000, 30_000, 30_000];
    for (const [index, wait] of waits.entries()) {
      await act(() => vi.advanceTimersByTimeAsync(wait - 1));
      expect(calls.list).toHaveBeenCalledTimes(index + 1);
      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(calls.list).toHaveBeenCalledTimes(index + 2);
    }
    // One timer at a time: the next ask, and nothing else.
    expect(vi.getTimerCount()).toBe(1);

    server.failLists = 0;
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(box().value).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops asking once a poll shows the note again", async () => {
    server.add("h1", "head", "Original");
    const { answer } = mount();
    server.failLists = 100;
    await answer([]);
    expect(calls.list).toHaveBeenCalledTimes(1);

    await answer(server.list());
    await act(() => vi.advanceTimersByTimeAsync(120_000));

    expect(calls.list).toHaveBeenCalledTimes(1);
    expect(box().value).toBe("Original");
  });

  it("does not ask again after a check refused with a 4xx", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    server.drop("h1");
    server.listFor = async () => {
      throw coded(404, "recording_not_found");
    };
    await poll();
    await act(() => vi.advanceTimersByTimeAsync(120_000));

    expect(calls.list).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels the check and its timer when the box goes away", async () => {
    server.add("h1", "head", "Original");
    const { poll, unmount } = mount();
    server.drop("h1");
    server.failLists = 100;
    await poll();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(calls.list).toHaveBeenCalledTimes(2);

    await unmount();
    await act(() => vi.advanceTimersByTimeAsync(120_000));

    expect(calls.list).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts a check still in flight when the box goes away", async () => {
    server.add("h1", "head", "Original");
    const { poll, unmount } = mount();
    server.drop("h1");
    let answerList!: () => void;
    server.listFor = () =>
      new Promise<TimelineNote[]>((resolve) => {
        answerList = () => resolve([]);
      });
    await poll();
    const [[, signal]] = calls.list.mock.calls as [string, AbortSignal][];
    expect(signal.aborted).toBe(false);

    await unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      answerList();
      await settle();
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops the old recording's checks when the box moves to another recording", async () => {
    server.add("h1", "head", "Original");
    let recordingId = RECORDING;
    let shown = server.list();
    const element = () => (
      <HeadNotePanel
        key={recordingId}
        recordingId={recordingId}
        timelineNotes={shown}
      />
    );
    const view = render(element());
    server.drop("h1");
    server.failLists = 100;
    shown = server.list();
    await act(async () => {
      view.rerender(element());
      await settle();
    });
    expect(calls.list).toHaveBeenCalledTimes(1);

    recordingId = "rec-2";
    shown = [];
    await act(async () => {
      view.rerender(element());
      await settle();
    });
    await act(() => vi.advanceTimersByTimeAsync(120_000));

    expect(calls.list).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("sends text typed back to what the note held after a lost edit, so the lost edit does not return", async () => {
    server.add("h1", "head", "A");
    const { poll } = mount();
    server.lose.add("update");
    type("AB");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(bodies()).toEqual(["AB"]);

    type("A");
    await waitForSave();
    await poll();

    // The first patch hears its own lost edit (409, own token), the second
    // puts "A" over it.
    expect(calls.update).toHaveBeenCalledTimes(3);
    expect(bodies()).toEqual(["A"]);
    expect(box().value).toBe("A");
    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not take a delete elsewhere for its own after a 409 showed its unanswered delete had not landed", async () => {
    server.add("h1", "head", "A");
    mount();
    // This device's delete never reaches the server.
    server.refuse.push({ write: "remove", error: new Error("Network Error") });
    type("");
    await waitForSave();
    server.edit("h1", "B's");
    type("C");
    await waitForSave();
    expect(screen.getByRole("alert")).toHaveTextContent("B's");
    await click("Use theirs");

    server.drop("h1");
    type("B's plus mine");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(server.notes).toEqual([]);
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("does not take a delete elsewhere for its own after a refused clear showed the note", async () => {
    server.add("h1", "head", "A");
    mount();
    server.refuse.push({ write: "remove", error: new Error("Network Error") });
    type("");
    await waitForSave();
    server.edit("h1", "B's");
    // Leaving the box clears it again, against the old revision.
    await blur();
    expect(screen.getByRole("alert")).toHaveTextContent("B's");
    await click("Use theirs");

    server.drop("h1");
    type("B's plus mine");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("does not take a delete elsewhere for its own after a save showed its unanswered delete had not landed", async () => {
    server.add("h1", "head", "A");
    mount();
    server.refuse.push({ write: "remove", error: new Error("Network Error") });
    type("");
    await waitForSave();
    // The edit is answered: the note is still there.
    type("Back again");
    await waitForSave();
    expect(bodies()).toEqual(["Back again"]);

    server.drop("h1");
    type("Back again, more");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "deleted on another device",
    );
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("takes its own delete answered with a server error as its own when typing again", async () => {
    server.add("h1", "head", "Original");
    mount();
    // The delete lands, but the proxy answers 502.
    server.answerWith.set("remove", refusal(502, "Bad Gateway"));
    type("");
    await waitForSave();
    expect(server.notes).toEqual([]);
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();

    type("Fresh start");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Fresh start"]);
  });

  it("leaves the visible save state to be read in browse mode, without a live role", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    type("Mine");
    await waitForSave();

    const saved = screen.getByText("Saved", SHOWN);
    expect(saved.closest("[aria-hidden]")).toBeNull();
    expect(saved).not.toHaveAttribute("role");
    expect(saved).not.toHaveAttribute("aria-live");

    await lock(true);
    expect(screen.getByText("Locked", SHOWN).closest("[aria-hidden]")).toBeNull();
  });

  it("announces a failed save once, not again on each failed retry", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.refuse.push({ write: "update", error: new Error("Network Error") });
    type("Mine");
    await waitForSave();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Save failed");

    const changes: string[] = [];
    const observer = new MutationObserver(() => {
      changes.push(status.textContent ?? "");
    });
    observer.observe(status, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    // The retry is held in flight, then its answer is lost.
    let release!: () => void;
    server.gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.lose.add("update");
    type("Mine, more");
    await waitForSave();
    expect(status).toHaveTextContent("Save failed");
    server.gate = null;
    await act(async () => {
      release();
      await settle();
    });
    observer.disconnect();

    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(changes).toEqual([]);
    expect(status).toHaveTextContent("Save failed");
  });

  it("leaves focus on the page when the lock ends after a click on no control", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    box().focus();
    await lock(true);
    const locked = screen.getByRole("group", { name: /temporarily locked/ });
    expect(locked).toHaveFocus();

    // A click on text, not a control, moves focus to the page.
    await act(async () => {
      locked.blur();
      await settle();
    });
    await lock(false);

    expect(box()).not.toHaveFocus();
    expect(document.body).toHaveFocus();
  });

  it("keeps the note typed after clearing a lost create, when the clear's delete lands late", async () => {
    const { poll } = mount();
    server.refuse.push({ write: "create", error: refusal(504, "Gateway Timeout") });
    type("Secret");
    await waitForSave();
    // The clear's delete gets no answer, and reaches the server only later.
    server.refuse.push({ write: "remove", error: refusal(504, "Gateway Timeout") });
    type("");
    await waitForSave();
    const [lostDelete] = calls.remove.mock.calls;
    type("Kept");
    await waitForSave();
    expect(bodies()).toEqual(["Kept"]);

    await server.remove(lostDelete[0], lostDelete[1], lostDelete[2]);
    await poll();
    expect(bodies()).toEqual(["Kept"]);
    type("Kept, more");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["Kept, more"]);
    // The cleared create's id is recorded, so that create cannot land either.
    const [cleared, kept] = calls.create.mock.calls.map(([, payload]) => payload.id);
    expect(kept).not.toBe(cleared);
    expect(server.tombstones.has(cleared!)).toBe(true);
  });

  it("keeps the text typed back after a lost edit, when that edit lands late", async () => {
    server.add("h1", "head", "A");
    const { poll } = mount();
    server.refuse.push({ write: "update", error: refusal(504, "Gateway Timeout") });
    type("AB");
    await waitForSave();
    const [lostEdit] = calls.update.mock.calls;
    type("A");
    await waitForSave();
    expect(bodies()).toEqual(["A"]);

    // The resend made a revision, so the lost edit is refused when it lands.
    await expect(
      server.update(lostEdit[0], lostEdit[1], lostEdit[2]),
    ).rejects.toMatchObject({ response: { status: 409 } });
    await poll();

    expect(bodies()).toEqual(["A"]);
    expect(box().value).toBe("A");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the text typed again after an unanswered clear, when that delete lands late", async () => {
    server.add("h1", "head", "A");
    const { poll } = mount();
    server.refuse.push({ write: "remove", error: refusal(504, "Gateway Timeout") });
    type("");
    await waitForSave();
    const [lostDelete] = calls.remove.mock.calls;
    type("A");
    await waitForSave();

    await expect(
      server.remove(lostDelete[0], lostDelete[1], lostDelete[2]),
    ).rejects.toMatchObject({ response: { status: 409 } });
    await poll();

    expect(bodies()).toEqual(["A"]);
    expect(box().value).toBe("A");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hands focus back to the box after the window lost focus during the lock", async () => {
    server.add("h1", "head", "Original");
    const { lock } = mount();
    box().focus();
    await lock(true);
    const locked = screen.getByRole("group", { name: /temporarily locked/ });

    // Another window takes focus: the lock blurs with nowhere for focus to go,
    // yet stays the page's focused element.
    await act(async () => {
      fireEvent.blur(locked, { relatedTarget: null });
      await settle();
    });
    expect(locked).toHaveFocus();
    await lock(false);

    expect(box()).toHaveFocus();
  });

  it("sends nothing for the note's own text once an answer showed the note an unanswered edit was for", async () => {
    server.add("h1", "head", "A");
    mount();
    server.refuse.push({ write: "update", error: refusal(504, "Gateway Timeout") });
    type("AB");
    await waitForSave();
    type("ABC");
    await waitForSave();
    expect(bodies()).toEqual(["ABC"]);
    // A validation refusal, whose detail is FastAPI's list: the save failed,
    // and nothing else changed.
    server.refuse.push({
      write: "update",
      error: refusal(422, [{ loc: ["body", "body"], msg: "invalid" }]),
    });
    type("ABCD");
    await waitForSave();
    expect(screen.getByText("Save failed", SHOWN)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const sent = calls.update.mock.calls.length;

    type("ABC");
    await waitForSave();

    // The note holds that text and no write to it is unanswered.
    expect(calls.update).toHaveBeenCalledTimes(sent);
    expect(server.notes[0].revision).toBe(2);
    expect(screen.getByText("Saved", SHOWN)).toBeInTheDocument();
  });

  /** A create lands with its answer lost; the clear after it never reaches the server. */
  const clearLostCreateOffline = async () => {
    server.lose.add("create");
    const view = mount();
    type("Agenda");
    await waitForSave();
    server.refuse.push({ write: "remove", error: new Error("Network Error") });
    type("");
    await waitForSave();
    expect(bodies()).toEqual(["Agenda"]);
    return view;
  };
  const otherNotes = () => screen.queryByRole("list", { name: "Other notes" });

  it("deletes a cleared lost create's note before the next write, when the clear never reached the server", async () => {
    const { poll } = await clearLostCreateOffline();

    type("Fresh");
    await waitForSave();
    await poll();

    expect(bodies()).toEqual(["Fresh"]);
    expect(otherNotes()).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The delete went again, under the token it first carried, before the create.
    const [first, again] = calls.remove.mock.calls;
    expect(again.slice(0, 2)).toEqual(first.slice(0, 2));
    expect(again[2]?.writeToken).toBe(first[2]?.writeToken);
    expect(calls.remove.mock.invocationCallOrder[1]).toBeLessThan(
      calls.create.mock.invocationCallOrder[1],
    );
    const [lost, fresh] = calls.create.mock.calls.map(([, payload]) => payload);
    expect(fresh.id).not.toBe(lost.id);
  });

  it("sends a cleared lost create's delete again when a poll shows its note", async () => {
    const { poll } = await clearLostCreateOffline();

    await poll();

    expect(bodies()).toEqual([]);
    expect(calls.remove).toHaveBeenCalledTimes(2);
    expect(box().value).toBe("");
    expect(otherNotes()).not.toBeInTheDocument();
  });

  it("does not list a note it is clearing among the others while its delete keeps failing", async () => {
    const { poll } = await clearLostCreateOffline();
    server.refuse.push({ write: "remove", error: new Error("Network Error") });

    await poll();

    expect(calls.remove).toHaveBeenCalledTimes(2);
    expect(bodies()).toEqual(["Agenda"]);
    expect(otherNotes()).not.toBeInTheDocument();
    expect(box().value).toBe("");
  });

  it("asks before a resent clear deletes a lost create's note edited on another device", async () => {
    await clearLostCreateOffline();
    server.edit(server.notes[0].id, "B's agenda");

    type("Fresh");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent("B's agenda");
    expect(bodies()).toEqual(["B's agenda"]);
    expect(calls.create).toHaveBeenCalledTimes(1);
  });

  /** As above, but a poll showed the create's note before the clear, which then gets no answer. */
  const clearPolledLostCreateOffline = async (failures: unknown[]) => {
    server.lose.add("create");
    const view = mount();
    type("Agenda");
    await waitForSave();
    await view.poll();
    for (const error of failures) server.refuse.push({ write: "remove", error });
    type("");
    await waitForSave();
    expect(calls.remove).toHaveBeenCalledTimes(1);
    return view;
  };
  const offline = () => new Error("Network Error");
  const after = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  it("sends a cleared lost create's delete again from 2 s, doubling to 30 s, with no new poll", async () => {
    await clearPolledLostCreateOffline(Array.from({ length: 6 }, offline));

    // The page passes on no poll: the notes it holds have not changed.
    for (const [wait, sent] of [
      [2_000, 2],
      [4_000, 3],
      [8_000, 4],
      [16_000, 5],
      [30_000, 6],
      [30_000, 7],
    ]) {
      await after(wait - 1);
      expect(calls.remove).toHaveBeenCalledTimes(sent - 1);
      await after(1);
      expect(calls.remove).toHaveBeenCalledTimes(sent);
    }

    expect(bodies()).toEqual([]);
    await after(120_000);
    expect(calls.remove).toHaveBeenCalledTimes(7);
    expect(otherNotes()).not.toBeInTheDocument();
  });

  it("stops sending a cleared create's delete again once the box goes away", async () => {
    const { unmount } = await clearPolledLostCreateOffline([offline(), offline()]);

    // Going away saves the unsaved clear once more, which fails too.
    await unmount();
    expect(calls.remove).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    await after(120_000);

    expect(calls.remove).toHaveBeenCalledTimes(2);
    expect(bodies()).toEqual(["Agenda"]);
  });

  it("does not send a cleared create's delete again after a refusal", async () => {
    await clearPolledLostCreateOffline([
      offline(),
      coded(404, "recording_not_found"),
    ]);

    await after(2_000);
    expect(calls.remove).toHaveBeenCalledTimes(2);
    await after(120_000);

    expect(calls.remove).toHaveBeenCalledTimes(2);
  });

  /** Another tab resends the note's own text under its token: a new revision of the same note. */
  const resendElsewhere = (id: string) => {
    const note = server.notes.find((candidate) => candidate.id === id)!;
    return server.update(RECORDING, id, {
      expected_revision: note.revision,
      body: note.body,
      write_token: OTHER_DEVICE,
    });
  };

  it("saves over a write elsewhere that left the note's text as it was, without asking", async () => {
    server.add("h1", "head", "A");
    mount();
    await resendElsewhere("h1");
    expect(server.notes[0].revision).toBe(2);

    type("A plus mine");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(bodies()).toEqual(["A plus mine"]);
    expect(
      calls.update.mock.calls.map(([, , update]) => update.expected_revision),
    ).toEqual([1, 2]);
  });

  it("clears a note over a write elsewhere that left its text as it was, without asking", async () => {
    server.add("h1", "head", "A");
    mount();
    await resendElsewhere("h1");

    type("");
    await waitForSave();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(server.notes).toEqual([]);
    expect(
      calls.remove.mock.calls.map(
        ([, , conditions]) => conditions?.expectedRevision,
      ),
    ).toEqual([1, 2]);
  });
});
