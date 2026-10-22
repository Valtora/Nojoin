"use client";

import React, { useEffect, useRef, useState } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import Button from "./ui/Button";
import Input from "./ui/Input";
import Modal from "./ui/Modal";

interface CreateTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Resolves to whether the tag was created. A refusal keeps the dialog open. */
  onConfirm: (tagName: string) => Promise<boolean>;
  title?: string;
  placeholder?: string;
  confirmText?: string;
  cancelText?: string;
}

export default function CreateTagModal({
  isOpen,
  onClose,
  onConfirm,
  title = "Create Tag",
  placeholder = "Tag name...",
  confirmText = "Create",
  cancelText = "Cancel",
}: CreateTagModalProps) {
  const [tagName, setTagName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useOnDepsChange([isOpen], () => {
    if (isOpen) setTagName("");
  });

  useEffect(() => {
    if (!isOpen) return;
    // Focus after the open transition, so the caret is not placed on an
    // element that is still animating in.
    const timer = setTimeout(() => inputRef.current?.focus(), 100);
    return () => clearTimeout(timer);
  }, [isOpen]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!tagName.trim() || submitting) return;
    setSubmitting(true);
    try {
      if (await onConfirm(tagName.trim())) onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {cancelText}
          </Button>
          <Button
            variant="primary"
            disabled={!tagName.trim() || submitting}
            onClick={() => void handleSubmit()}
          >
            {confirmText}
          </Button>
        </>
      }
    >
      <form onSubmit={(e) => void handleSubmit(e)}>
        <Input
          ref={inputRef}
          type="text"
          value={tagName}
          onChange={(e) => setTagName(e.target.value)}
          placeholder={placeholder}
          aria-label={title}
        />
      </form>
    </Modal>
  );
}
