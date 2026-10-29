import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useNoteDrag } from "./_hooks/useNoteDrag";
import type { MoveTarget } from "@/lib/notes/noteMoves";

const originalElementFromPoint = document.elementFromPoint;
afterEach(() => {
  document.elementFromPoint = originalElementFromPoint;
});

function DraggingNote({ revision, onDrop }: { revision: number; onDrop: (id: string, target: MoveTarget) => void }) {
  const regionRef = useRef<HTMLDivElement>(null);
  const { start } = useNoteDrag({ regionRef, onDrop,
    targetAfter: (index) => ({ afterIndex: index, anchorMs: 1000, source: { id: "open", revision } }) });
  return <div ref={regionRef}>
    <button onPointerDown={(event) => start(event, "note", 0)}>Grip</button>
    <p data-order-index="1">Destination</p>
  </div>;
}

describe("dragging while a live destination updates", () => {
  it("uses the current destination revision and latest note callback even without another pointer move", () => {
    const oldDrop = vi.fn();
    const latestDrop = vi.fn();
    const view = render(<DraggingNote revision={1} onDrop={oldDrop} />);
    document.elementFromPoint = () => screen.getByText("Destination");
    fireEvent.pointerDown(screen.getByRole("button", { name: "Grip" }), { button: 0, pointerType: "mouse", clientX: 2, clientY: 2 });
    fireEvent.pointerMove(window, { buttons: 1, clientX: 3, clientY: 3 });
    view.rerender(<DraggingNote revision={2} onDrop={latestDrop} />);
    fireEvent.pointerUp(window);
    expect(oldDrop).not.toHaveBeenCalled();
    expect(latestDrop).toHaveBeenCalledWith("note", { afterIndex: 1, anchorMs: 1000, source: { id: "open", revision: 2 } });
    expect(document.body.style.cursor).toBe("");
  });
});
