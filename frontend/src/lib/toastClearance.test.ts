// @vitest-environment node

import { describe, expect, it } from "vitest";

import { TOAST_BAR_GAP_PX, TOAST_REST_PX, toastLift } from "./toastClearance";

const box = (top: number, bottom: number, left: number, right: number) => ({
  top,
  bottom,
  left,
  right,
});

// A 390 x 844 phone: toasts span the screen less 1rem a side.
const PHONE = { height: 844, stack: { left: 16, right: 374, height: 72 } };
const DOCKED_BAR = box(780, 844, 0, 390);

describe("toastLift", () => {
  it("raises the stack to sit just above a bar it would cover (#974)", () => {
    const lift = toastLift(PHONE.stack, [DOCKED_BAR], PHONE.height);

    // The stack's bottom edge ends a gap above the bar's top edge.
    expect(PHONE.height - TOAST_REST_PX - lift).toBe(DOCKED_BAR.top - TOAST_BAR_GAP_PX);
  });

  it("raises an empty stack too, so the first toast appears above the bar", () => {
    expect(toastLift({ ...PHONE.stack, height: 0 }, [DOCKED_BAR], PHONE.height)).toBeGreaterThan(0);
  });

  it("clears the tallest of several bars", () => {
    const lift = toastLift(PHONE.stack, [DOCKED_BAR, box(700, 844, 0, 390)], PHONE.height);

    expect(PHONE.height - TOAST_REST_PX - lift).toBe(700 - TOAST_BAR_GAP_PX);
  });

  it("leaves the stack where it rests beside a bar it does not share columns with", () => {
    // A desktop: toasts at the bottom right, the batch bar in the rail at the left.
    const stack = { left: 976, right: 1424, height: 72 };

    expect(toastLift(stack, [box(836, 900, 64, 384)], 900)).toBe(0);
  });

  it("leaves the stack where it rests below a bar higher up the screen", () => {
    // A short list: the batch bar follows the last card, mid-screen.
    expect(toastLift(PHONE.stack, [box(300, 364, 0, 390)], PHONE.height)).toBe(0);
  });

  it("ignores a bar that is not laid out", () => {
    expect(toastLift(PHONE.stack, [box(0, 0, 0, 0)], PHONE.height)).toBe(0);
  });
});
