import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import ContextMenu from "./ContextMenu";

/**
 * The shared menu, as the recordings list opens it from a row's actions
 * button (issue #737): placed against the button, an ARIA menu that takes
 * focus, and focus handed back to the button whichever way it closes.
 */

const MENU = { width: 192, height: 100 };

const rect = (box: { left: number; top: number; width: number; height: number }) =>
  ({
    ...box,
    x: box.left,
    y: box.top,
    right: box.left + box.width,
    bottom: box.top + box.height,
    toJSON: () => ({}),
  }) as DOMRect;

/** Gives the menu its size; jsdom lays nothing out. */
const sizeMenus = () =>
  vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      return this.getAttribute("role") === "menu"
        ? rect({ left: 0, top: 0, ...MENU })
        : rect({ left: 0, top: 0, width: 0, height: 0 });
    });

const anchorAt = (box: { left: number; top: number; width: number; height: number }) => {
  const anchor = document.createElement("button");
  anchor.textContent = "Actions";
  document.body.appendChild(anchor);
  anchor.getBoundingClientRect = () => rect(box);
  return anchor;
};

const menuStyle = () => (screen.getByRole("menu") as HTMLElement).style;

describe("ContextMenu", () => {
  afterEach(() => {
    // Unmounted first: the menus are portals into the body.
    cleanup();
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("is a named menu of menu items", () => {
    render(
      <ContextMenu
        x={10}
        y={10}
        label="Actions for Alpha review"
        items={[{ label: "Rename", onClick: vi.fn() }]}
        onClose={vi.fn()}
      />,
    );

    const menu = screen.getByRole("menu", { name: "Actions for Alpha review" });
    expect(menu).toContainElement(screen.getByRole("menuitem", { name: "Rename" }));
  });

  it("sits below its anchor with their right edges aligned, and above it near the foot", () => {
    sizeMenus();
    const near = anchorAt({ left: 280, top: 100, width: 28, height: 28 });
    const { unmount } = render(
      <ContextMenu x={0} y={0} anchor={near} items={[]} onClose={vi.fn()} />,
    );
    expect(menuStyle().left).toBe(`${308 - MENU.width}px`);
    expect(menuStyle().top).toBe(`${128 + 4}px`);
    unmount();

    const low = anchorAt({ left: 280, top: window.innerHeight - 40, width: 28, height: 28 });
    render(<ContextMenu x={0} y={0} anchor={low} items={[]} onClose={vi.fn()} />);
    expect(menuStyle().top).toBe(`${window.innerHeight - 40 - 4 - MENU.height}px`);
  });

  it("follows its anchor when the list scrolls", () => {
    sizeMenus();
    let top = 100;
    const anchor = anchorAt({ left: 280, top, width: 28, height: 28 });
    anchor.getBoundingClientRect = () => rect({ left: 280, top, width: 28, height: 28 });
    render(<ContextMenu x={0} y={0} anchor={anchor} items={[]} onClose={vi.fn()} />);

    top = 60;
    fireEvent.scroll(document.body);

    expect(menuStyle().top).toBe(`${60 + 28 + 4}px`);
  });

  it("closes when its anchor leaves the page", () => {
    sizeMenus();
    const onClose = vi.fn();
    const anchor = anchorAt({ left: 280, top: 100, width: 28, height: 28 });
    render(<ContextMenu x={0} y={0} anchor={anchor} items={[]} onClose={onClose} />);

    anchor.remove();
    fireEvent.scroll(document.body);

    expect(onClose).toHaveBeenCalled();
  });

  it("leaves a press on its anchor to the anchor, and closes on a press anywhere else", () => {
    const onClose = vi.fn();
    const anchor = anchorAt({ left: 280, top: 100, width: 28, height: 28 });
    render(<ContextMenu x={0} y={0} anchor={anchor} items={[]} onClose={onClose} />);

    fireEvent.mouseDown(anchor);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  describe("with focus to give back", () => {
    const items = ["Rename", "Archive", "Move to Bin"];

    /** The menu as a row's button opens it, closing as the list closes it. */
    const Opened = ({ onSelect = vi.fn() }: { onSelect?: (label: string) => void }) => {
      const [opener, setOpener] = useState<HTMLButtonElement | null>(null);
      const [open, setOpen] = useState(true);
      return (
        <>
          <button ref={setOpener} type="button">
            Actions for Alpha review
          </button>
          <button type="button">Next row</button>
          {open && opener && (
            <ContextMenu
              x={0}
              y={0}
              anchor={opener}
              returnFocusTo={opener}
              items={items.map((label) => ({ label, onClick: () => onSelect(label) }))}
              onClose={() => setOpen(false)}
            />
          )}
        </>
      );
    };

    const opener = () => screen.getByRole("button", { name: "Actions for Alpha review" });
    const item = (name: string) => screen.getByRole("menuitem", { name });

    it("takes focus to its first item and moves with the arrows, Home and End", () => {
      render(<Opened />);

      expect(item("Rename")).toHaveFocus();
      fireEvent.keyDown(item("Rename"), { key: "ArrowDown" });
      expect(item("Archive")).toHaveFocus();
      fireEvent.keyDown(item("Archive"), { key: "End" });
      expect(item("Move to Bin")).toHaveFocus();
      fireEvent.keyDown(item("Move to Bin"), { key: "ArrowDown" });
      expect(item("Rename")).toHaveFocus();
      fireEvent.keyDown(item("Rename"), { key: "ArrowUp" });
      expect(item("Move to Bin")).toHaveFocus();
      fireEvent.keyDown(item("Move to Bin"), { key: "Home" });
      expect(item("Rename")).toHaveFocus();
    });

    it("keeps its items out of the Tab order, reached by the arrows instead", () => {
      render(<Opened />);

      for (const label of items) expect(item(label)).toHaveAttribute("tabindex", "-1");
    });

    it.each(["Escape", "Tab"])("closes on %s and gives focus back to its opener", (key) => {
      render(<Opened />);

      fireEvent.keyDown(item("Archive"), { key });

      expect(screen.queryByRole("menu")).toBeNull();
      expect(opener()).toHaveFocus();
    });

    it("gives focus back before the chosen item runs, so the item can move it on", () => {
      let focusedWhenRun: Element | null = null;
      render(
        <Opened
          onSelect={() => {
            focusedWhenRun = document.activeElement;
          }}
        />,
      );

      fireEvent.click(item("Archive"));

      expect(focusedWhenRun).toBe(opener());
      expect(screen.queryByRole("menu")).toBeNull();
    });
  });

  it("leaves focus alone when it has nowhere to give it back", () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();

    render(
      <ContextMenu
        x={10}
        y={10}
        items={[{ label: "Rename", onClick: vi.fn() }]}
        onClose={vi.fn()}
      />,
    );

    expect(outside).toHaveFocus();
    expect(screen.getByRole("menuitem", { name: "Rename" })).not.toHaveAttribute("tabindex");
  });
});
