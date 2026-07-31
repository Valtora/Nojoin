"use client";

import React, { useEffect, useRef, useState } from "react";

import Button from "./ui/Button";
import Input from "./ui/Input";
import Modal from "./ui/Modal";

interface CreateTagModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (tagName: string) => void;
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
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    setTagName("");
    // Focus after the open transition, so the caret is not placed on an
    // element that is still animating in.
    const timer = setTimeout(() => inputRef.current?.focus(), 100);
    return () => clearTimeout(timer);
  }, [isOpen]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (tagName.trim()) {
      onConfirm(tagName.trim());
      onClose();
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
          <Button variant="primary" disabled={!tagName.trim()} onClick={() => handleSubmit()}>
            {confirmText}
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit}>
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
