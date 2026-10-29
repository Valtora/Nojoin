import { fireEvent, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { GlobalSpeaker } from "@/types";
import { fillMarkerViolations, renderWithFillContract as render } from "@/test/fillMarkerContracts";
import { PeopleTable } from "./PeopleTable";

const person: GlobalSpeaker = {
  id: 7, name: "Dana", tags: [], created_at: "2026-06-15T10:00:00Z", updated_at: "2026-06-15T10:00:00Z",
};

function People() {
  const [selectedIds, setSelectedIds] = useState(new Set<number>());
  return <PeopleTable people={[person]} isLoading={false} selectedIds={selectedIds}
    onToggleSelection={(id) => setSelectedIds((ids) => ids.has(id) ? new Set() : new Set([id]))}
    onSelectAll={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />;
}

describe("people card fill contract", () => {
  it("opens details from the name in both layouts without selecting the person", () => {
    const onEdit = vi.fn();
    const onToggleSelection = vi.fn();
    render(<PeopleTable people={[person]} isLoading={false} selectedIds={new Set()}
      onToggleSelection={onToggleSelection} onSelectAll={vi.fn()} onEdit={onEdit} onDelete={vi.fn()} />);
    const names = screen.getAllByRole("button", { name: "Details for Dana" });
    expect(names).toHaveLength(2);
    names.forEach((name) => fireEvent.click(name));
    expect(onEdit.mock.calls).toEqual([[person], [person]]);
    expect(onToggleSelection).not.toHaveBeenCalled();
  });

  it("checks the mobile card as its checkbox selects and deselects the person", () => {
    const { baseElement } = render(<People />);
    const card = screen.getAllByText("Dana").map((name) => name.closest('[data-ui-surface="card"]'))
      .find((node) => node?.classList.contains("p-4"))!;
    const checkbox = card.querySelector('input[type="checkbox"]')!;
    expect(card).toHaveClass("bg-surface-card");
    fireEvent.click(checkbox);
    expect(card).toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(checkbox);
    expect(card).toHaveClass("bg-surface-card");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
  });
});
