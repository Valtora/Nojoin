'use client';

import { useState, useEffect } from 'react';
import { Link as LinkIcon } from 'lucide-react';

import Button from './ui/Button';
import Input from './ui/Input';
import Modal from './ui/Modal';

interface LinkModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (url: string) => void;
  initialUrl?: string;
}

export default function LinkModal({ isOpen, onClose, onSubmit, initialUrl = '' }: LinkModalProps) {
  const [url, setUrl] = useState(initialUrl);

  useEffect(() => {
    if (isOpen) {
      setUrl(initialUrl);
    }
  }, [isOpen, initialUrl]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    let finalUrl = url.trim();
    if (finalUrl && !/^https?:\/\//i.test(finalUrl)) {
      finalUrl = 'https://' + finalUrl;
    }
    onSubmit(finalUrl);
    onClose();
  };

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      size="sm"
      title={
        <span className="flex items-center gap-2">
          <LinkIcon aria-hidden="true" className="w-4 h-4" />
          {initialUrl ? 'Edit Link' : 'Insert Link'}
        </span>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => handleSubmit()}>
            Save
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit}>
        <Input
          type="text"
          label="URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com"
          autoFocus
        />
      </form>
    </Modal>
  );
}
