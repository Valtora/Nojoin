import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Badge, { StatusBadge } from "./Badge";
import Button from "./Button";
import Card from "./Card";
import IconButton from "./IconButton";
import Input from "./Input";
import Modal from "./Modal";
import Select from "./Select";
import { Switch } from "./Switch";
import { RecordingStatus } from "@/types";

/**
 * These assert the contracts the sweep depends on rather than the exact
 * classes, which are expected to move. The two that do check classes are the
 * ones the design rules turn on: no primitive may emit a raw palette utility,
 * and no primitive may raise a shadow outside the float exception.
 */

const PALETTE = /\b(?:bg|text|border|ring|from|via|to|fill|stroke|divide|outline|decoration|placeholder|accent|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/;

describe("Button", () => {
  it("defaults to type=button so it cannot submit a form by accident", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "button");
  });

  it("disables itself and reports busy while loading", () => {
    render(<Button loading>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("keeps its label while loading so the control does not resize", () => {
    render(<Button loading>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("suppresses the leading icon while loading", () => {
    render(
      <Button loading iconLeft={<span data-testid="icon" />}>
        Save
      </Button>,
    );
    expect(screen.queryByTestId("icon")).not.toBeInTheDocument();
  });

  it("does not fire onClick while loading", () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Save
      </Button>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it.each(["primary", "secondary", "ghost", "danger"] as const)(
    "renders the %s variant without a raw palette utility",
    (variant) => {
      render(<Button variant={variant}>Go</Button>);
      expect(screen.getByRole("button", { name: "Go" }).className).not.toMatch(PALETTE);
    },
  );

  it.each(["primary", "secondary", "ghost", "danger"] as const)(
    "renders the %s variant without a shadow",
    (variant) => {
      render(<Button variant={variant}>Go</Button>);
      expect(screen.getByRole("button", { name: "Go" }).className).not.toMatch(/\bshadow-/);
    },
  );
});

describe("IconButton", () => {
  it("names itself from the required aria-label", () => {
    render(<IconButton aria-label="Delete recording" icon={<span />} />);
    expect(screen.getByRole("button", { name: "Delete recording" })).toBeInTheDocument();
  });

  it("keeps a 40px target at the smallest size", () => {
    render(<IconButton aria-label="Edit" size="sm" icon={<span />} />);
    expect(screen.getByRole("button", { name: "Edit" }).className).toContain("h-10");
  });
});

describe("Card", () => {
  it("is not focusable or clickable by default", () => {
    const { container } = render(<Card>Body</Card>);
    expect(container.firstElementChild?.className).not.toContain("cursor-pointer");
  });

  it("tints rather than lifts when interactive", () => {
    const { container } = render(<Card interactive>Body</Card>);
    const className = container.firstElementChild?.className ?? "";
    expect(className).toContain("hover:bg-action-tint");
    expect(className).not.toMatch(/hover:shadow-/);
  });
});

describe("Badge", () => {
  it("maps a processing recording to the info tone with a spinner", () => {
    const { container } = render(<StatusBadge status={RecordingStatus.PROCESSING} />);
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).not.toBeNull();
  });

  it("maps a processed recording to the success tone", () => {
    const { container } = render(<StatusBadge status={RecordingStatus.PROCESSED} />);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain("bg-status-success-bg");
  });

  it("overrides the label when notes are generating", () => {
    render(<StatusBadge status={RecordingStatus.PROCESSED} generatingNotes />);
    expect(screen.getByText("Generating notes")).toBeInTheDocument();
  });

  it("covers every recording status", () => {
    for (const status of Object.values(RecordingStatus)) {
      const { unmount } = render(<StatusBadge status={status} />);
      unmount();
    }
  });

  it("renders each tone without a raw palette utility", () => {
    for (const tone of ["neutral", "info", "success", "warning", "danger"] as const) {
      const { container, unmount } = render(<Badge tone={tone}>Label</Badge>);
      expect(container.firstElementChild?.className).not.toMatch(PALETTE);
      unmount();
    }
  });
});

describe("Input", () => {
  it("associates its label with the field", () => {
    render(<Input label="Meeting name" />);
    expect(screen.getByLabelText("Meeting name")).toBeInTheDocument();
  });

  it("describes the field with its hint", () => {
    render(<Input label="Name" hint="Shown in the sidebar" />);
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription("Shown in the sidebar");
  });

  it("marks itself invalid and shows the error instead of the hint", () => {
    render(<Input label="Name" hint="Shown in the sidebar" error="Name is required" />);
    const field = screen.getByLabelText("Name");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription("Name is required");
    expect(screen.queryByText("Shown in the sidebar")).not.toBeInTheDocument();
  });
});

describe("Select", () => {
  it("associates its label and reports the chosen value", () => {
    const onChange = vi.fn();
    render(
      <Select label="Language" onChange={onChange} defaultValue="en">
        <option value="en">English</option>
        <option value="fr">French</option>
      </Select>,
    );
    const field = screen.getByLabelText("Language");
    fireEvent.change(field, { target: { value: "fr" } });
    expect(onChange).toHaveBeenCalled();
  });
});

describe("Switch", () => {
  it("exposes its state through role and aria-checked", () => {
    render(<Switch checked onCheckedChange={() => {}} />);
    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("toggles to the opposite value", () => {
    const onCheckedChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});

describe("Modal", () => {
  it("renders nothing when closed", () => {
    render(
      <Modal open={false} onClose={() => {}} title="Confirm">
        Body
      </Modal>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders its title, body and footer when open", () => {
    render(
      <Modal open onClose={() => {}} title="Confirm" footer={<button>Delete</button>}>
        Body
      </Modal>,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Confirm")).toBeInTheDocument();
    expect(screen.getByText("Body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("closes from the header control", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Confirm">
        Body
      </Modal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("omits the close control when asked", () => {
    render(
      <Modal open onClose={() => {}} title="Confirm" hideCloseButton>
        Body
      </Modal>,
    );
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });

  it("uses a plain scrim with no backdrop filter", () => {
    const { baseElement } = render(
      <Modal open onClose={() => {}} title="Confirm">
        Body
      </Modal>,
    );
    expect(baseElement.innerHTML).not.toMatch(/backdrop-blur/);
    expect(baseElement.querySelector(".bg-scrim")).not.toBeNull();
  });
});
