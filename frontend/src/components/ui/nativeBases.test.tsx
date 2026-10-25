import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ButtonBase, InputBase, SelectBase, TextareaBase } from "./nativeBases";
import { SurfaceBase } from "./SurfaceBase";

describe("native bases", () => {
  it.each([undefined, "plain", "action"] as const)("marks %s button chrome", (chrome) => {
    render(<ButtonBase chrome={chrome}>Action</ButtonBase>);
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("data-ui", "button");
    expect(button).toHaveAttribute("data-ui-chrome", chrome ?? "plain");
  });

  it.each([undefined, "text", "email", "number", "date", "datetime-local", "time", "month", "week"])(
    "marks %s inputs for field chrome",
    (type) => {
      const { container } = render(<InputBase type={type} />);
      expect(container.firstElementChild).toHaveAttribute("data-ui", "input");
      expect(container.firstElementChild).toHaveAttribute("data-ui-input", "field");
    },
  );

  it.each([
    ["select", <SelectBase key="select"><option>English</option></SelectBase>],
    ["textarea", <TextareaBase key="textarea" />],
  ])("marks the %s host", (marker, control) => {
    const { container } = render(control);
    expect(container.firstElementChild).toHaveAttribute("data-ui", marker);
  });

  it("preserves implicit submission, external form association and disabled clicks", () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    const onClick = vi.fn();
    const { rerender } = render(
      <>
        <form id="native-form" onSubmit={onSubmit}>
          <InputBase name="name" defaultValue="Synthetic meeting" />
        </form>
        <ButtonBase form="native-form" onClick={onClick}>Save</ButtonBase>
      </>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).not.toHaveAttribute("type");

    fireEvent.click(button);

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const form = document.getElementById("native-form") as HTMLFormElement;
    expect(new FormData(form).get("name")).toBe("Synthetic meeting");

    rerender(<ButtonBase disabled onClick={onClick}>Save</ButtonBase>);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("forwards refs to focusable hosts in the original child order", () => {
    const button = createRef<HTMLButtonElement>();
    const input = createRef<HTMLInputElement>();
    const select = createRef<HTMLSelectElement>();
    const textarea = createRef<HTMLTextAreaElement>();
    const onKeyDown = vi.fn();
    const { container } = render(
      <>
        <ButtonBase ref={button}>Action</ButtonBase>
        <InputBase ref={input} aria-label="Name" onKeyDown={onKeyDown} />
        <SelectBase ref={select} aria-label="Language">
          <option>English</option>
        </SelectBase>
        <TextareaBase ref={textarea} aria-label="Notes" />
      </>,
    );

    expect([...container.children].map((element) => element.tagName)).toEqual([
      "BUTTON", "INPUT", "SELECT", "TEXTAREA",
    ]);
    for (const ref of [button, input, select, textarea]) {
      ref.current?.focus();
      expect(ref.current).toHaveFocus();
    }
    fireEvent.keyDown(input.current!, { key: "Enter" });
    expect(onKeyDown).toHaveBeenCalledTimes(1);
    expect(onKeyDown.mock.calls[0][0].key).toBe("Enter");
  });

  it("renders the original DOM, class conflicts and inline styles without field wrappers", () => {
    const className = "px-2  px-4 text-contrast-helper text-contrast-icon-muted";
    const style = { width: "37px", borderRadius: "3px" };
    const native = render(
      <>
        <button className={className} style={style}><span>Go</span></button>
        <input className={className} style={style} size={7} defaultValue="Name" />
        <select className={className} style={style} size={2} defaultValue="en">
          <option value="en">English</option>
        </select>
        <textarea className={className} style={style} rows={3} defaultValue="Notes" />
      </>,
    );
    const original = native.container.innerHTML;
    native.unmount();
    const migrated = render(
      <>
        <ButtonBase className={className} style={style}><span>Go</span></ButtonBase>
        <InputBase className={className} style={style} size={7} defaultValue="Name" />
        <SelectBase className={className} style={style} size={2} defaultValue="en">
          <option value="en">English</option>
        </SelectBase>
        <TextareaBase className={className} style={style} rows={3} defaultValue="Notes" />
      </>,
    );

    for (const host of migrated.container.children) {
      for (const attribute of [...host.attributes]) {
        if (attribute.name.startsWith("data-ui")) host.removeAttribute(attribute.name);
      }
    }
    expect(migrated.container.innerHTML).toBe(original);
  });

  it("preserves native change events and uncontrolled selection state", () => {
    const onChange = vi.fn();
    render(
      <>
        <InputBase type="checkbox" aria-label="Enabled" defaultChecked onChange={onChange} />
        <SelectBase aria-label="Language" defaultValue="en" onChange={onChange}>
          <option value="en">English</option>
          <option value="fr">French</option>
        </SelectBase>
      </>,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fr" } });
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("combobox")).toHaveValue("fr");
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it.each(["checkbox", "radio", "range", "file", "color", "hidden", "button", "image", "reset", "submit"])(
    "keeps %s outside text-field chrome",
    (type) => {
      const { container } = render(<InputBase type={type} />);
      const input = container.firstElementChild;
      expect(input).toHaveAttribute("data-ui", "input");
      expect(input).toHaveAttribute("data-ui-input", "native");
      expect(input).not.toHaveAttribute("class");
      expect(input).not.toHaveAttribute("id");
    },
  );
});

describe("SurfaceBase", () => {
  it.each(["card", "inset", "float", "pane"] as const)("marks %s surfaces on the default host", (kind) => {
    const { container } = render(<SurfaceBase kind={kind} />);
    expect(container.firstElementChild?.tagName).toBe("DIV");
    expect(container.firstElementChild).toHaveAttribute("data-ui", "surface");
    expect(container.firstElementChild).toHaveAttribute("data-ui-surface", kind);
  });

  it("preserves semantic hosts, refs, child structure and bubbling clicks", () => {
    const ref = createRef<HTMLElement>();
    const onClick = vi.fn();
    const { container } = render(
      <SurfaceBase as="aside" kind="pane" ref={ref} onClick={onClick} className="p-2 p-4">
        <ButtonBase>Open</ButtonBase>
      </SurfaceBase>,
    );
    expect(ref.current).toBe(container.firstElementChild);
    expect(ref.current?.tagName).toBe("ASIDE");
    expect(ref.current?.className).toBe("p-2 p-4");
    expect(ref.current?.children).toHaveLength(1);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(ref.current).not.toHaveAttribute("kind");
    expect(ref.current).not.toHaveAttribute("as");
  });
});
