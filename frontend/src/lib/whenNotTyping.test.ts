import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";

import { isTextEntry, whenNotTyping } from "./whenNotTyping";

const add = <K extends keyof HTMLElementTagNameMap>(tag: K, attributes: Record<string, string> = {}) => {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  document.body.append(element);
  return element;
};

describe("isTextEntry", () => {
  afterEach(() => document.body.replaceChildren());

  it.each(["text", "search", "email", "url", "tel", "password", "number", "date", "not-a-type"])(
    "counts an input of type %s", (type) => {
      expect(isTextEntry(add("input", { type }))).toBe(true);
    },
  );

  it.each(["button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit"])(
    "leaves out an input of type %s", (type) => {
      expect(isTextEntry(add("input", { type }))).toBe(false);
    },
  );

  it("counts a textarea, but not a read-only field", () => {
    expect(isTextEntry(add("textarea"))).toBe(true);
    expect(isTextEntry(add("textarea", { readonly: "" }))).toBe(false);
    expect(isTextEntry(add("input", { type: "search", readonly: "" }))).toBe(false);
  });

  it("counts a Tiptap editor and anything inside it, but not a widget it marks uneditable", () => {
    const editor = add("div", { class: "ProseMirror", contenteditable: "true", role: "textbox" });
    editor.innerHTML = '<p><strong>Alice</strong> said</p><div contenteditable="false"><button>Chip</button></div>';
    expect(isTextEntry(editor)).toBe(true);
    expect(isTextEntry(editor.querySelector("strong"))).toBe(true);
    expect(isTextEntry(add("div", { contenteditable: "" }))).toBe(true);
    expect(isTextEntry(add("div", { contenteditable: "plaintext-only" }))).toBe(true);
    expect(isTextEntry(editor.querySelector("button"))).toBe(false);
  });

  it("leaves out everything else", () => {
    expect(isTextEntry(null)).toBe(false);
    expect(isTextEntry(document.body)).toBe(false);
    expect(isTextEntry(add("button"))).toBe(false);
    expect(isTextEntry(add("select"))).toBe(false);
    expect(isTextEntry(add("a", { href: "#" }))).toBe(false);
  });
});

describe("whenNotTyping", () => {
  const DELAY = 1500;
  const IDLE = 1000;
  let run: Mock<() => void>;
  const advance = (ms: number) => vi.advanceTimersByTime(ms);
  const press = (target: Element, key = "a") => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  };
  const click = (target: Element) => {
    target.dispatchEvent(new Event("pointerdown", { bubbles: true }));
  };
  const compose = (target: Element, phase: "start" | "end") => {
    target.dispatchEvent(new CompositionEvent(`composition${phase}`, { bubbles: true }));
  };

  beforeEach(() => {
    vi.useFakeTimers();
    run = vi.fn<() => void>();
  });
  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("runs after the delay when no text field has focus", () => {
    add("button").focus();
    whenNotTyping(run, DELAY, IDLE);
    advance(DELAY - 1);
    expect(run).not.toHaveBeenCalled();
    advance(1);
    expect(run).toHaveBeenCalledOnce();
  });

  it.each([
    ["an input", () => add("input", { type: "search" })],
    ["a textarea", () => add("textarea")],
    ["a contenteditable editor", () => add("div", { contenteditable: "true", tabindex: "0" })],
  ])("holds while %s has focus, then runs once focus has left it and the page is quiet", (_, make) => {
    const field = make();
    field.focus();
    whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    for (let key = 0; key < 20; key++) {
      press(field);
      advance(300);
    }
    advance(10_000);
    expect(run).not.toHaveBeenCalled();

    field.blur();
    advance(IDLE - 1);
    expect(run).not.toHaveBeenCalled();
    advance(1);
    expect(run).toHaveBeenCalledOnce();
  });

  it("starts the quiet wait again on each key press or click after leaving the field", () => {
    const field = add("input");
    const button = add("button");
    field.focus();
    whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    button.focus();
    advance(IDLE - 100);
    press(button, "Tab");
    advance(IDLE - 100);
    click(document.body);
    advance(IDLE - 1);
    expect(run).not.toHaveBeenCalled();
    advance(1);
    expect(run).toHaveBeenCalledOnce();
  });

  it("holds again when focus comes back into a field before the wait ends", () => {
    const field = add("input");
    field.focus();
    whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    field.blur();
    advance(IDLE - 100);
    field.focus();
    advance(10_000);
    expect(run).not.toHaveBeenCalled();
    field.blur();
    advance(IDLE);
    expect(run).toHaveBeenCalledOnce();
  });

  it("holds while an input method's composition is open, wherever focus is", () => {
    // A custom widget that isTextEntry does not recognise.
    const widget = add("div", { tabindex: "0", role: "combobox" });
    widget.focus();
    whenNotTyping(run, DELAY, IDLE);
    compose(widget, "start");
    advance(DELAY + 10_000);
    expect(run).not.toHaveBeenCalled();
    compose(widget, "end");
    advance(IDLE);
    expect(run).toHaveBeenCalledOnce();
  });

  it("ends a composition when its element loses focus, with or without compositionend", () => {
    const widget = add("div", { tabindex: "0" });
    widget.focus();
    whenNotTyping(run, DELAY, IDLE);
    compose(widget, "start");
    advance(DELAY);
    widget.blur();
    advance(IDLE);
    expect(run).toHaveBeenCalledOnce();
  });

  it("runs after a field removed by its own key press, which takes focus without a focusout", () => {
    const field = add("input");
    field.focus();
    whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    const focusout = vi.fn();
    document.addEventListener("focusout", focusout);
    press(field, "Enter");
    field.remove();
    document.removeEventListener("focusout", focusout);
    expect(focusout).not.toHaveBeenCalled();
    advance(IDLE);
    expect(run).toHaveBeenCalledOnce();
  });

  it("leaves no timer or listener behind once it has run or been cancelled", () => {
    const added = vi.spyOn(document, "addEventListener");
    const removed = vi.spyOn(document, "removeEventListener");
    const pairs = (spy: typeof added) => spy.mock.calls.map(([type, listener]) => [type, listener]);
    const field = add("input");

    field.focus();
    const cancelWaiting = whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    field.blur();
    cancelWaiting();
    expect(vi.getTimerCount()).toBe(0);
    expect(pairs(removed)).toEqual(expect.arrayContaining(pairs(added)));

    added.mockClear();
    removed.mockClear();
    const cancelAfterRun = whenNotTyping(run, DELAY, IDLE);
    advance(DELAY);
    expect(run).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    expect(pairs(added)).toHaveLength(6);
    expect(pairs(removed)).toEqual(expect.arrayContaining(pairs(added)));
    cancelAfterRun();

    field.focus();
    field.blur();
    press(document.body);
    advance(10_000);
    expect(run).toHaveBeenCalledOnce();
  });
});
