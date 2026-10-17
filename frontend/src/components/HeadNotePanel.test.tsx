import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote, TimelineNoteCreate, TimelineNoteUpdate } from "@/types";

import HeadNotePanel from "./HeadNotePanel";

/**
 * The real panel and the real store against a fake of the notes API that
 * behaves like the server: per-note revisions, 409 with the current note on a
 * stale edit, 404 for a deleted note, and a retried create returning the
 * first note as it was made.
 */
class FakeServer {
  notes: TimelineNote[] = [];
  private clock = 0;
  /** The next create commits but its response is lost. */
  loseNextCreateResponse = false;

  add(id: string, kind: TimelineNote["kind"], body: string, author = "mira") {
    this.clock += 1;
    const at = new Date(Date.UTC(2026, 9, 17, 10, 0, this.clock)).toISOString();
    const note: TimelineNote = {
      id,
      recording_id: "rec-1",
      kind,
      body,
      revision: 1,
      created_by: { id: 1, username: author },
      created_at: at,
      updated_at: at,
    };
    this.notes.push(note);
    this.notes.sort((a, b) => a.created_at.localeCompare(b.created_at));
    return note;
  }

  edit(id: string, body: string) {
    const note = this.notes.find((candidate) => candidate.id === id)!;
    note.body = body;
    note.revision += 1;
    return { ...note };
  }

  list = () => this.notes.map((note) => ({ ...note }));

  create = async (_recording: string, payload: TimelineNoteCreate) => {
    const existing = this.notes.find((note) => note.id === payload.id);
    if (existing) return { ...existing };
    const created = this.add(payload.id!, payload.kind, payload.body.trim());
    if (this.loseNextCreateResponse) {
      this.loseNextCreateResponse = false;
      throw new Error("Network Error");
    }
    return { ...created };
  };

  update = async (_recording: string, id: string, update: TimelineNoteUpdate) => {
    const note = this.notes.find((candidate) => candidate.id === id);
    if (!note) throw { isAxiosError: true, response: { status: 404, data: {} } };
    if (note.revision !== update.expected_revision) {
      throw {
        isAxiosError: true,
        response: {
          status: 409,
          data: { detail: { message: "changed", note: { ...note } } },
        },
      };
    }
    return this.edit(id, update.body!.trim());
  };

  remove = async (_recording: string, id: string, expectedRevision?: number) => {
    const note = this.notes.find((candidate) => candidate.id === id);
    if (!note) throw { isAxiosError: true, response: { status: 404, data: {} } };
    if (expectedRevision !== undefined && note.revision !== expectedRevision) {
      throw {
        isAxiosError: true,
        response: {
          status: 409,
          data: { detail: { message: "changed", note: { ...note } } },
        },
      };
    }
    this.notes = this.notes.filter((candidate) => candidate.id !== id);
  };
}

let server: FakeServer;
const calls = { create: vi.fn(), update: vi.fn(), remove: vi.fn() };

vi.mock("@/lib/api", () => ({
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

const mount = () => {
  const view = render(
    <HeadNotePanel recordingId="rec-1" timelineNotes={server.list()} />,
  );
  return {
    poll: () =>
      act(() => {
        view.rerender(
          <HeadNotePanel recordingId="rec-1" timelineNotes={server.list()} />,
        );
      }),
  };
};

const box = () => screen.getByRole("textbox") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const waitForSave = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(1800);
  });
const blur = () =>
  act(async () => {
    fireEvent.blur(box());
    await vi.advanceTimersByTimeAsync(0);
  });

describe("HeadNotePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    server = new FakeServer();
    calls.create.mockReset();
    calls.update.mockReset();
    calls.remove.mockReset();
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
    expect(calls.update).toHaveBeenLastCalledWith("rec-1", "h1", {
      expected_revision: 1,
      body: "Original, mine",
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Theirs");
    expect(screen.getByText("Changed elsewhere")).toBeInTheDocument();

    // Leaving the box does not save over their version unasked.
    await blur();
    expect(calls.update).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(calls.update).toHaveBeenLastCalledWith("rec-1", "h1", {
      expected_revision: 2,
      body: "Original, mine",
    });
    expect(server.notes[0].body).toBe("Original, mine");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("takes the other version when asked", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    server.edit("h1", "Theirs");
    await poll();
    await waitForSave();

    fireEvent.click(screen.getByRole("button", { name: "Use theirs" }));

    expect(box().value).toBe("Theirs");
    await blur();
    expect(calls.update).toHaveBeenCalledTimes(1);
    expect(server.notes[0].body).toBe("Theirs");
  });

  it("finishes a create whose response was lost with the newer text", async () => {
    server.loseNextCreateResponse = true;
    mount();

    type("Agenda");
    await waitForSave();
    expect(screen.getByText("Save failed")).toBeInTheDocument();

    type("Agenda, budget");
    await waitForSave();

    const [first, second] = calls.create.mock.calls.map(([, payload]) => payload.id);
    expect(second).toBe(first);
    expect(server.notes).toHaveLength(1);
    expect(server.notes[0].body).toBe("Agenda, budget");
    expect(box().value).toBe("Agenda, budget");
  });

  it("keeps a just-created note while polls still show none", async () => {
    const { poll } = mount();
    type("Agenda");
    await waitForSave();
    const created = server.list();
    server.notes = [];
    await poll();
    expect(box().value).toBe("Agenda");
    server.notes = created;

    type("Agenda, budget");
    await waitForSave();
    expect(calls.create).toHaveBeenCalledTimes(1);
    expect(server.notes[0].body).toBe("Agenda, budget");
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
    expect(server.notes.map((note) => note.body)).toEqual([
      "From device B",
      "From device A, edited",
    ]);
  });

  it("empties the box once polls show another device deleted the note", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    await waitForSave();
    await poll();

    await server.remove("rec-1", "h1");
    await poll();
    // One poll could be an older answer arriving late.
    expect(box().value).toBe("Mine");
    await poll();
    expect(box().value).toBe("");
  });

  it("does not take a poll answered out of order for a delete", async () => {
    const view = render(<HeadNotePanel recordingId="rec-1" timelineNotes={[]} />);
    type("Agenda");
    await waitForSave();
    const fresh = server.list();
    act(() => {
      view.rerender(<HeadNotePanel recordingId="rec-1" timelineNotes={fresh} />);
    });
    // A slower, earlier poll arrives after the fresh one.
    act(() => {
      view.rerender(<HeadNotePanel recordingId="rec-1" timelineNotes={[]} />);
    });
    expect(box().value).toBe("Agenda");

    type("Agenda, budget");
    await waitForSave();
    expect(server.notes.map((note) => note.body)).toEqual(["Agenda, budget"]);
  });

  it("refuses to clear notes another device changed meanwhile", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("");
    server.edit("h1", "Theirs, important");
    await poll();
    await waitForSave();

    expect(server.notes.map((note) => note.body)).toEqual(["Theirs, important"]);
    expect(screen.getByRole("alert")).toHaveTextContent("Theirs, important");
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
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(server.notes[0].body).toBe("Original");
    expect(box().value).toBe("Original");
  });

  it("saves nothing when only outer whitespace changed", async () => {
    const { poll } = mount();
    type("Item one\n");
    await waitForSave();
    await poll();
    type("Item one");
    await waitForSave();

    expect(calls.create).toHaveBeenCalledTimes(1);
    expect(calls.update).not.toHaveBeenCalled();
  });

  it("asks before re-creating a note deleted under an edit", async () => {
    server.add("h1", "head", "Original");
    const { poll } = mount();
    type("Mine");
    await server.remove("rec-1", "h1");
    await poll();
    await waitForSave();

    expect(screen.getByRole("alert")).toHaveTextContent("deleted on another device");
    expect(calls.create).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Keep mine" }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(server.notes.map((note) => note.body)).toEqual(["Mine"]);
  });

  it("leaves a trailing new line in the box when the saved note comes back trimmed", async () => {
    const { poll } = mount();
    type("Item one\n");
    await waitForSave();
    await poll();

    expect(server.notes[0].body).toBe("Item one");
    expect(box().value).toBe("Item one\n");
  });

  it("deletes the note when the box is cleared, and a stale poll cannot restore it", async () => {
    server.add("h1", "head", "Original");
    const stale = server.list();
    const view = render(<HeadNotePanel recordingId="rec-1" timelineNotes={stale} />);

    type("");
    await waitForSave();
    expect(server.notes).toEqual([]);

    act(() => {
      view.rerender(<HeadNotePanel recordingId="rec-1" timelineNotes={stale} />);
    });
    expect(box().value).toBe("");
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
});
