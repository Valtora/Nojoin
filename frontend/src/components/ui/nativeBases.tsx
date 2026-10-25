import { forwardRef, type ComponentPropsWithoutRef } from "react";

export type ButtonChrome = "action" | "plain";

export interface ButtonBaseProps extends ComponentPropsWithoutRef<"button"> {
  /** Classifies chrome without adding styling or changing native behavior. */
  chrome?: ButtonChrome;
}

/** Native controls with inert style hooks; classes and native defaults stay untouched. */
export const ButtonBase = forwardRef<HTMLButtonElement, ButtonBaseProps>(function ButtonBase(
  { chrome = "plain", ...props },
  ref,
) {
  return <button {...props} ref={ref} data-ui="button" data-ui-chrome={chrome} />;
});

const NATIVE_INPUT_TYPES = new Set([
  "button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit",
]);

export const InputBase = forwardRef<HTMLInputElement, ComponentPropsWithoutRef<"input">>(
  function InputBase(props, ref) {
    return (
      <input
        {...props}
        ref={ref}
        data-ui="input"
        data-ui-input={NATIVE_INPUT_TYPES.has(props.type?.toLowerCase() ?? "text") ? "native" : "field"}
      />
    );
  },
);

export const SelectBase = forwardRef<HTMLSelectElement, ComponentPropsWithoutRef<"select">>(
  function SelectBase(props, ref) {
    return <select {...props} ref={ref} data-ui="select" />;
  },
);

export const TextareaBase = forwardRef<HTMLTextAreaElement, ComponentPropsWithoutRef<"textarea">>(
  function TextareaBase(props, ref) {
    return <textarea {...props} ref={ref} data-ui="textarea" />;
  },
);
