import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fillMarkerViolations, renderWithFillContract as render } from "@/test/fillMarkerContracts";
import TaskDeadlineModal from "./TaskDeadlineModal";

afterEach(() => vi.useRealTimers());

describe("deadline preset fill contracts", () => {
  it("checks selected and unselected day and time presets before and after clicks", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T10:00:00Z"));
    const { baseElement } = render(
      <TaskDeadlineModal isOpen taskTitle="Prepare agenda" value={null} timeZone="UTC"
        onClose={vi.fn()} onSave={vi.fn()} />,
    );
    const days = ["Today", "Tomorrow", "Next week"];
    const times = ["9 AM", "Noon", "5 PM", "6 PM"];
    const check = (selectedDay: string, selectedTime: string) => {
      for (const name of [...days, ...times]) {
        expect(screen.getByRole("button", { name }))
          .toHaveClass(name === selectedDay || name === selectedTime ? "bg-action" : "bg-surface-card");
      }
      expect(fillMarkerViolations(baseElement)).toEqual([]);
    };
    check("Today", "5 PM");
    for (const day of ["Tomorrow", "Next week", "Today"]) {
      fireEvent.click(screen.getByRole("button", { name: day }));
      check(day, "5 PM");
    }
    for (const time of times) {
      fireEvent.click(screen.getByRole("button", { name: time }));
      check("Today", time);
    }
  });
});

describe("deadline draft", () => {
  it("keeps a pick when the parent re-renders with the same deadline", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // Monday 15 June 2026; the saved deadline is Saturday the 20th at 09:00.
    vi.setSystemTime(new Date("2026-06-15T10:00:00Z"));
    const saved = "2026-06-20T09:00:00Z";
    const modal = (value: Date) => (
      <TaskDeadlineModal isOpen taskTitle="Prepare agenda" value={value} timeZone="UTC"
        onClose={vi.fn()} onSave={vi.fn()} />
    );
    const { rerender } = render(modal(new Date(saved)));

    fireEvent.click(screen.getByRole("button", { name: "Tomorrow" }));
    expect(screen.getByText("Tuesday, 16 June at 9:00 AM")).toBeInTheDocument();

    // A new Date for the same instant, as a parent builds on every render.
    rerender(modal(new Date(saved)));
    expect(screen.getByText("Tuesday, 16 June at 9:00 AM")).toBeInTheDocument();

    // A different deadline starts the draft again.
    rerender(modal(new Date("2026-06-22T09:00:00Z")));
    expect(screen.getByText("Monday, 22 June at 9:00 AM")).toBeInTheDocument();
  });
});
