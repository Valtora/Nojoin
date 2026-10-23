import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  discardRecordingCapture: vi.fn(),
}));

vi.mock("./client", () => ({
  default: { post: mocks.post },
  API_BASE_URL: "/api/v1",
}));

vi.mock("./capture", () => ({
  discardRecordingCapture: mocks.discardRecordingCapture,
}));

import { importAudio } from "./recordings";

const MB = 1024 * 1024;

/** A file of three 10 MB parts, without allocating 25 MB. */
const largeFile = () => {
  const file = new File(["x"], "quarterly-review.mp4");
  Object.defineProperty(file, "size", { value: 25 * MB });
  return file;
};

const answers = (...outcomes: ("ok" | Error)[]) => {
  mocks.post.mockImplementation(async (url: string) => {
    if (url.startsWith("/recordings/import/chunked/init")) {
      return { data: { id: "rec-1" } };
    }
    const outcome = outcomes.shift() ?? "ok";
    if (outcome instanceof Error) {
      throw outcome;
    }
    return { data: { id: "rec-1", status: "QUEUED" } };
  });
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("importAudio (issue #569)", () => {
  it("discards the import it started when a part fails, and still fails", async () => {
    const failure = new Error("Network Error");
    answers("ok", failure);
    mocks.discardRecordingCapture.mockResolvedValue(undefined);

    await expect(importAudio(largeFile())).rejects.toBe(failure);

    expect(mocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", "import_failed");
    expect(mocks.post).not.toHaveBeenCalledWith(
      expect.stringContaining("/finalize"),
    );
  });

  it("reports the part's failure when the discard fails too", async () => {
    const failure = new Error("Network Error");
    answers(failure);
    mocks.discardRecordingCapture.mockRejectedValue(new Error("Still offline"));

    await expect(importAudio(largeFile())).rejects.toBe(failure);
  });

  it("keeps the import when only its finalize failed, which may have queued it", async () => {
    answers("ok", "ok", "ok", new Error("Network Error"));

    await expect(importAudio(largeFile())).rejects.toThrow("Network Error");

    expect(mocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("discards nothing when the import finishes", async () => {
    answers();

    await expect(importAudio(largeFile())).resolves.toMatchObject({ status: "QUEUED" });

    expect(mocks.discardRecordingCapture).not.toHaveBeenCalled();
  });
});
