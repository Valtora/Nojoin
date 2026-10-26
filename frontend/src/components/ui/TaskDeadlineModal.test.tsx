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
