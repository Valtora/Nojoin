import { fireEvent, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fillMarkerViolations, renderWithFillContract as render } from "@/test/fillMarkerContracts";
import MonthCalendar from "./MonthCalendar";

function Calendar() {
  const [selectedDay, onSelectDay] = useState<string | null>(null);
  return <MonthCalendar month={new Date(2026, 5, 1)} markedDays={new Set(["2026-06-15"])}
    selectedDay={selectedDay} onSelectDay={onSelectDay} onMonthChange={vi.fn()} />;
}

describe("month calendar fill contract", () => {
  it("checks empty and selected fills as the selected day moves", () => {
    const { baseElement } = render(<Calendar />);
    const first = screen.getByRole("button", { name: "15" });
    const next = screen.getByRole("button", { name: "16" });
    expect(first).not.toHaveClass("bg-action");
    fireEvent.click(first);
    expect(first).toHaveClass("bg-action");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(next);
    expect(first).not.toHaveClass("bg-action");
    expect(next).toHaveClass("bg-action");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
  });
});
