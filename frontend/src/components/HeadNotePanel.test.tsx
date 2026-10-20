import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote, TimelineNoteCreate, TimelineNoteUpdate } from "@/types";

import HeadNotePanel from "./HeadNotePanel";

const RECORDING = "rec-1";

// The code points Python's str.isspace() accepts: what the API's strip()
// trims from a note's body.
const PYTHON_SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009,
  0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
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

type Write = "create" | "update" | "remove";

/**
 * The real panel and the real store against a fake of the notes routes that
 * behaves like them: notes scoped to the recording, the caller as author,
 * per-note revisions, 409 with the current note on a stale edit or delete,
 * 404 for a missing note, 422 for an empty or too long body after Python's
 * strip, a retried create returning the note as it is now, an edit that
 * changes nothing keeping its revision, writes that commit but lose their
 * response, and requests refused before they run.
 */
class FakeServer {
  notes: TimelineNote[] = [];
  private clock = 0;
  /** The next write of each kind commits, but its response is lost. */
  readonly lose = new Set<Write>();
  /** The next create's new id turns out to be held on another recording. */
  nextIdTaken = false;
  /** Writes wait for this before they run. */
  gate: Promise<void> | null = null;
  /** Refusals for the next writes, in order, each before its write runs. */
  readonly refuse: { write: Write; error: unknown }[] = [];
  /** How many of the next lists fail without an answer. */
  failLists = 0;

  add(id: string, kind: TimelineNote["kind"], body: string, author = "dana") {
    this.clock += 1;
    const at = new Date(Date.UTC(2026, 9, 17, 10, 0, this.clock)).toISOString();
    const note: TimelineNote = {
      id,
      recording_id: RECORDING,
      kind,
      body,
      revision: 1,
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
  }

  /** Another device's delete. */
  drop(id: string) {
    this.notes = this.notes.filter((candidate) => candidate.id !== id);
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
    if (this.nextIdTaken) {
      this.nextIdTaken = false;
      throw refusal(409, "That note id is already in use");
    }
    const created = this.add(payload.id!, payload.kind, body);
    this.respond("create");
    return { ...created };
  };

  update = async (recordingId: string, id: string, update: TimelineNoteUpdate) => {
    this.scope(recordingId);
    this.refused("update");
    const body = update.body == null ? null : this.clean(update.body);
    if (this.gate) await this.gate;
    const note = this.find(id);
    if (note.revision !== update.expected_revision) {
      throw refusal(409, { message: "changed", note: { ...note } });
    }
    if (body === null || body === note.body) return { ...note };
    note.body = body;
    note.revision += 1;
    this.respond("update");
    return { ...note };
  };

  remove = async (recordingId: string, id: string, expectedRevision?: number) => {
    this.scope(recordingId);
    this.refused("remove");
    const note = this.find(id);
    if (expectedRevision !== undefined && note.revision !== expectedRevision) {
      throw refusal(409, { message: "changed", note: { ...note } });
    }
    this.drop(id);
    this.respond("remove");
  };

  private scope(recordingId: string) {
    if (recordingId !== RECORDING) throw refusal(404, "Recording not found");
  }

  private find(id: string) {
    const note = this.notes.find((candidate) => candidate.id === id);
    if (!note) throw refusal(404, "Note not found");
    return note;
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
  }
}

let server: FakeServer;
const calls = { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() };

vi.mock("@/lib/api", () => ({
  listTimelineNotes: (...args: [string]) => {
    calls.list(...args);
    return server.listFor(...args);
  },
  createTimelineNote: (...args: [string, TimelineNoteCreate]) => {
    calls.create(...args);
    return server.create(...args);
  },
  updateTimelineNote: (...args: [string, string, TimelineNoteUpdate]) => {
    calls.update(...args);
    return server.update(...args);
  },
  deleteTimelineNote: (...args: [string, string, number?]) => {
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
    <HeadNotePanel recordingId={RECORDING} timelineNotes={shown} disabled={disabled} />
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
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
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
    expect(calls.update).toHaveBeenLastCalledWith(RECORDING, "h1", {
      expected_revision: 1,
      body: "Original, mine",
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Theirs");
    expect(screen.getByText("Changed elsewhere")).toBeInTheDocument();

    // Leaving the box does not save over their version unasked.
    await blur();
    expect(calls.update).toHaveBeenCalledTimes(1);

    await click("Keep mine");
    expect(calls.update).toHaveBeenLastCalledWith(RECORDING, "h1", {
      expected_revision: 2,
      body: "Original, mine",
    });
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
    expect(screen.getByText("Save failed")).toBeInTheDocument();

    type("Agenda, budget");
    await waitForSave();

    const [first, second] = calls.create.mock.calls.map(([, payload]) => payload.id);
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
    expect(screen.getByRole("alert")).toHaveTextContent("Device B: the decision");
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
    expect(screen.getByText("Save failed")).toBeInTheDocument();

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
    expect(calls.remove).toHaveBeenCalledWith(RECORDING, id, 1);
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

    expect(screen.queryByRole("list", { name: "Other notes" })).not.toBeInTheDocument();
  });

  it("makes the note under a new id when its id is taken elsewhere", async () => {
    server.nextIdTaken = true;
    mount();
    type("Agenda");
    await waitForSave();

    const [first, second] = calls.create.mock.calls.map(([, payload]) => payload.id);
    expect(second).not.toBe(first);
    expect(server.notes.map((note) => [note.id, note.body])).toEqual([[second, "Agenda"]]);
    expect(screen.getByText("Saved")).toBeInTheDocument();
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

    expect(calls.remove).toHaveBeenCalledWith(RECORDING, "h1", 1);
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
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("asks before re-creating a note deleted under an edit", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.drop("h1");
    await poll();
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent("deleted on another device");
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
    expect(screen.getByText(/Shorten the notes to save them/)).toBeInTheDocument();
    expect(screen.getByText("Save failed")).toBeInTheDocument();
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
    expect(screen.getByRole("alert")).toHaveTextContent("deleted on another device");
    expect(calls.create).toHaveBeenCalledTimes(1);

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
    server.refuse.push({ write: "remove", error: refusal(404, "Recording not found") });
    type("");
    await waitForSave();
    expect(screen.getByText("Save failed")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // The box still edits that note, rather than making a second one.
    type("Original, kept");
    await waitForSave();
    expect(bodies()).toEqual(["Original, kept"]);
  });

  it("shows a note whose create lands after the box was cleared", async () => {
    const { poll } = mount();
    server.refuse.push({ write: "create", error: refusal(504, "Gateway Timeout") });
    type("Secret");
    await waitForSave();
    expect(screen.getByText("Save failed")).toBeInTheDocument();
    const [[, { id }]] = calls.create.mock.calls;

    type("");
    await waitForSave();
    // The proxy gave up on the create, but the backend commits it now.
    server.add(id, "head", "Secret");
    await poll();

    expect(box().value).toBe("Secret");
  });

  it("takes its own delete whose response was lost as done when typing again", async () => {
    server.add("h1", "head", "Original");
    mount();
    server.lose.add("remove");
    type("");
    await waitForSave();
    expect(screen.getByText("Save failed")).toBeInTheDocument();

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
    expect(screen.getByText("Save failed")).toBeInTheDocument();

    type("");
    await waitForSave();

    expect(calls.remove.mock.calls.map(([, , revision]) => revision)).toEqual([1, 2]);
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
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(server.notes).toEqual([]);
  });

  it("forgets texts older than its last three unanswered saves", async () => {
    server.add("h1", "head", "Original");
    mount();
    for (const text of ["a", "b", "c", "d"]) {
      server.refuse.push({ write: "update", error: new Error("Network Error") });
      type(text);
      await waitForSave();
    }
    // Another device writes what this one tried first, four saves ago.
    server.edit("h1", "a");

    type("e");
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent("changed on another device");
    expect(bodies()).toEqual(["a"]);
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

    expect(screen.getByText("Saved")).toBeInTheDocument();
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
    expect(document.activeElement).toHaveTextContent("Notes are temporarily locked");
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
    expect(screen.getByRole("alert")).toHaveTextContent("deleted on another device");

    type("");
    expect(screen.queryByRole("button", { name: "Keep mine" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard mine" })).not.toBeInTheDocument();
    await click("Dismiss");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(box().value).toBe("");
    await waitForSave();
    expect(server.notes).toEqual([]);
  });

  it("counts text with Windows line ends as the box will send it", () => {
    server.add("h1", "head", "abcdefg\r\n".repeat(6_000));
    mount();

    expect(screen.getByText(/47,999 \/ 50,000 characters/)).toBeInTheDocument();
    expect(screen.queryByText(/Shorten the notes/)).not.toBeInTheDocument();
  });
});
