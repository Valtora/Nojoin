"use client";

import { useImportAudioStore } from "@/lib/importAudioStore";

import ImportAudioModal from "../ImportAudioModal";

const handleImportSuccess = () => {
  window.dispatchEvent(new CustomEvent("recording-updated"));
};

/**
 * Renders the one Import Audio dialog, bound to the shared open state. The
 * dialog returns focus on close to the control that opened it: Headless UI
 * records the click on that control, which also covers phones, where tapping a
 * button does not focus it.
 */
export default function ImportAudioHost() {
  const isOpen = useImportAudioStore((state) => state.isImportAudioOpen);
  const close = useImportAudioStore((state) => state.closeImportAudio);

  return (
    <ImportAudioModal
      isOpen={isOpen}
      onClose={close}
      onSuccess={handleImportSuccess}
    />
  );
}
