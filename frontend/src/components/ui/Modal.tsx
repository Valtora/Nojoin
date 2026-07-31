"use client";

import { Fragment, type ReactNode } from "react";
import { Dialog, Transition, TransitionChild } from "@headlessui/react";
import { X } from "lucide-react";

import { cn } from "@/lib/cn";
import IconButton from "./IconButton";

export type ModalSize = "sm" | "md" | "lg" | "xl";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  /** Rendered in a divided row at the foot of the panel. Usually buttons. */
  footer?: ReactNode;
  size?: ModalSize;
  /** Suppresses the close control, for a modal that must be resolved by its own actions. */
  hideCloseButton?: boolean;
  /** Blocks dismissal by scrim click or Escape. Use only for destructive confirmations mid-flight. */
  dismissible?: boolean;
  className?: string;
  children: ReactNode;
}

const SIZES: Record<ModalSize, string> = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

/**
 * The single modal implementation. Headless UI supplies the focus trap, the
 * scroll lock, the Escape handling and the portal, so what this adds is the
 * visual contract: a plain scrim with no backdrop filter, the float surface and
 * shadow, and the z-index taken from the token scale rather than picked per
 * call site.
 *
 * The height cap and the viewport gutter are not decoration. Without them a
 * tall modal on a phone renders its actions below the fold with no way to
 * reach them, which is the failure the previous hand-rolled scrims all shared.
 */
export default function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  size = "md",
  hideCloseButton = false,
  dismissible = true,
  className,
  children,
}: ModalProps) {
  return (
    <Transition appear show={open} as={Fragment}>
      <Dialog
        as="div"
        className="relative z-[var(--z-modal)]"
        onClose={dismissible ? onClose : () => {}}
      >
        <TransitionChild
          as={Fragment}
          enter="ease-out duration-200"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-150"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-scrim" aria-hidden="true" />
        </TransitionChild>

        <div className="fixed inset-0 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <TransitionChild
              as={Fragment}
              enter="ease-out duration-200"
              enterFrom="opacity-0 scale-95"
              enterTo="opacity-100 scale-100"
              leave="ease-in duration-150"
              leaveFrom="opacity-100 scale-100"
              leaveTo="opacity-0 scale-95"
            >
              <Dialog.Panel
                className={cn(
                  "flex w-full flex-col overflow-hidden text-left align-middle",
                  "max-h-[calc(100dvh-2rem)]",
                  "rounded-surface-panel border border-surface-float-border bg-surface-float shadow-float",
                  SIZES[size],
                  className,
                )}
              >
                {(title || !hideCloseButton) && (
                  <div className="flex items-start justify-between gap-4 border-b border-surface-divider px-5 py-4">
                    <div className="min-w-0">
                      {title && (
                        <Dialog.Title className="truncate text-base font-semibold text-foreground">
                          {title}
                        </Dialog.Title>
                      )}
                      {description && (
                        <Dialog.Description className="mt-1 text-sm text-contrast-helper">
                          {description}
                        </Dialog.Description>
                      )}
                    </div>
                    {!hideCloseButton && (
                      <IconButton
                        aria-label="Close"
                        size="sm"
                        icon={<X aria-hidden="true" />}
                        onClick={onClose}
                        className="-mr-2 -mt-1"
                      />
                    )}
                  </div>
                )}

                <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

                {footer && (
                  <div className="flex flex-wrap items-center justify-end gap-2 border-t border-surface-divider px-5 py-4">
                    {footer}
                  </div>
                )}
              </Dialog.Panel>
            </TransitionChild>
          </div>
        </div>
      </Dialog>
    </Transition>
  );
}
