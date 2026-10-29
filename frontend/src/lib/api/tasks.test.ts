import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
vi.mock("./client", () => ({ default: { get } }));

import { getUserTasks } from "./tasks";

describe("task list query parameters", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: [] });
  });

  it("encodes multiple tag IDs as repeated parameters understood by FastAPI", async () => {
    await getUserTasks("all", [301, 302]);

    const [path, options] = get.mock.calls[0];
    expect(path).toBe("/tasks/");
    expect(options.params.toString()).toBe("status=all&tag_ids=301&tag_ids=302");
  });

  it("keeps the dashboard's default active query free of tag filters", async () => {
    await getUserTasks();

    expect(get.mock.calls[0][1].params.toString()).toBe("status=active");
  });
});
