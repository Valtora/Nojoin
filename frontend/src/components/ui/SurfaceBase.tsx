import { createElement, type ComponentPropsWithRef } from "react";

export type SurfaceKind = "card" | "inset" | "float" | "pane";
export type SurfaceTag = "div" | "section" | "article" | "aside" | "span" | "ul";

export type SurfaceBaseProps<T extends SurfaceTag = "div"> = {
  as?: T;
  kind: SurfaceKind;
} & Omit<ComponentPropsWithRef<NoInfer<T>>, "as">;

/** A surface marker on the original native element, without wrappers or chrome. */
export function SurfaceBase<T extends SurfaceTag = "div">({
  as,
  kind,
  ...props
}: SurfaceBaseProps<T>) {
  return createElement(as ?? "div", {
    ...props,
    "data-ui": "surface",
    "data-ui-surface": kind,
  });
}
