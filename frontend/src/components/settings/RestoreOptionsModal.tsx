'use client';

import { InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { AlertTriangle, FileArchive } from 'lucide-react';
import { useState } from 'react';
import { useOnDepsChange } from '@/lib/useOnDepsChange';
import { Switch } from '@/components/ui/Switch';

import Button from '../ui/Button';
import Modal from '../ui/Modal';

interface RestoreOptionsModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: (clearExisting: boolean, overwriteExisting: boolean) => void;
    fileName: string;
}

export default function RestoreOptionsModal({
    isOpen,
    onClose,
    onConfirm,
    fileName,
}: RestoreOptionsModalProps) {
    const [clearExisting, setClearExisting] = useState(false);
    const [overwriteExisting, setOverwriteExisting] = useState(false);

    // Reset state when opened
    useOnDepsChange([isOpen], () => {
        if (isOpen) {
            setClearExisting(false);
            setOverwriteExisting(false);
        }
    });

    const optionClass = (selected: boolean) =>
        `flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
            selected
                ? 'border-action bg-action-tint'
                : 'border-surface-border hover:bg-surface-inset'
        }`;

    return (
        <Modal
            open={isOpen}
            onClose={onClose}
            size="lg"
            title={
                <span className="flex items-center gap-3">
                    <span className="rounded-lg bg-action-tint p-2">
                        <FileArchive aria-hidden="true" className="h-6 w-6 text-action-tint-fg" />
                    </span>
                    <span>
                        <span className="block">Restore a backup</span>
                        <span className="block text-sm font-normal text-contrast-helper">{fileName}</span>
                    </span>
                </span>
            }
            footer={
                <>
                    <Button variant="ghost" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button
                        variant="primary"
                        onClick={() => {
                            onConfirm(clearExisting, overwriteExisting);
                            onClose();
                        }}
                    >
                        Start restore
                    </Button>
                </>
            }
        >
            <div className="space-y-6">
                {/* Option 1: Clear Existing Data */}
                <SurfaceBase
                    kind="inset" className="rounded-lg border border-surface-border bg-surface-inset p-4">
                    <div className="mb-2 flex items-center justify-between gap-4">
                        <span id="restore-clear-label" className="text-sm font-semibold text-foreground">
                            Clear all existing data
                        </span>
                        <Switch
                            aria-labelledby="restore-clear-label"
                            aria-describedby="restore-clear-help"
                            checked={clearExisting}
                            onCheckedChange={setClearExisting}
                        />
                    </div>
                    <div className="flex items-start gap-2 text-xs text-status-warning-fg">
                        <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                        <p id="restore-clear-help">
                            Deletes every current recording and setting before the restore.
                            User accounts are kept, so nobody is locked out.
                        </p>
                    </div>
                </SurfaceBase>

                {/* Option 2: Conflict Resolution (Only if NOT clearing data) */}
                {!clearExisting && (
                    <div className="rounded-lg border border-surface-border p-4">
                        <h4 className="mb-3 text-sm font-semibold text-foreground">
                            Meetings that are already here
                        </h4>
                        <p className="mb-4 text-xs text-contrast-helper">
                            What happens to a meeting in the backup that this installation already has.
                        </p>

                        <div className="space-y-3">
                            {/* Skip Option */}
                            <label className={optionClass(!overwriteExisting)}>
                                <InputBase
                                    type="radio"
                                    name="conflict_resolution"
                                    className="mt-1 accent-action"
                                    checked={!overwriteExisting}
                                    onChange={() => setOverwriteExisting(false)}
                                />
                                <div>
                                    <span className="block text-sm font-medium text-foreground">
                                        Skip
                                    </span>
                                    <span className="mt-1 block text-xs text-contrast-helper">
                                        Keeps the current version and adds only the meetings that are new.
                                    </span>
                                </div>
                            </label>

                            {/* Overwrite Option */}
                            <label className={optionClass(overwriteExisting)}>
                                <InputBase
                                    type="radio"
                                    name="conflict_resolution"
                                    className="mt-1 accent-action"
                                    checked={overwriteExisting}
                                    onChange={() => setOverwriteExisting(true)}
                                />
                                <div>
                                    <span className="block text-sm font-medium text-foreground">
                                        Overwrite
                                    </span>
                                    <span className="mt-1 block text-xs text-contrast-helper">
                                        Deletes the current version and puts the backup&apos;s copy in its place.
                                    </span>
                                </div>
                            </label>
                        </div>
                    </div>
                )}
            </div>
        </Modal>
    );
}
