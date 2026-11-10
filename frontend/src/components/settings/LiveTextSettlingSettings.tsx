import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { ButtonBase, SelectBase } from "@/components/ui/nativeBases";
import { cn } from "@/lib/cn";
import type { LiveTextSettlingMode, Settings } from "@/types";
import SettingsRow from "./SettingsRow";
import { SETTINGS_SELECT_CLASS } from "./settingsControls";

interface Props {
  settings: Settings;
  isAdmin: boolean;
  onPersist: (settings: Settings) => void | Promise<void>;
}

/**
 * Seconds of audio between settling steps: one capture segment
 * (CAPTURE_SEGMENT_SECONDS in lib/capture/recorder.ts, which the test pins).
 */
export const SETTLING_STEP_S = 2;

const FINE_TUNE_IDS = ["transcription-live-settling-k", "transcription-live-settling-window"];

/**
 * Whether a word can come out the same `k` more times before it leaves the
 * audio checked again. A word first heard at the newest step leaves the window
 * once `window` seconds have passed, and its start can sit up to a step
 * earlier, so `k` further steps fit only when the window is longer than
 * `k + 1` steps. Otherwise words can be fixed as heard when they leave the
 * window: at K=5 with a 10 s window every word is, the failure #730 measured.
 */
export function settlingCanAgree(k: number, windowS: number): boolean {
  return windowS > SETTLING_STEP_S * (k + 1);
}

const checksLabel = (value: number) =>
  `After ${value} matching ${value === 1 ? "check" : "checks"}`;

/** Live word settling: what the live transcript does, with its timing tucked away. */
export default function LiveTextSettlingSettings({ settings, isAdmin, onPersist }: Props) {
  const k = settings.live_text_settling_k ?? 3;
  const windowS = settings.live_text_settling_window_s ?? 10;
  const mode = settings.live_text_settling ?? "auto";
  // Open on arrival when a search result points inside it. The block mounts
  // only once Advanced has opened in the browser, so the hash is readable.
  const [open, setOpen] = useState(
    () => typeof window !== "undefined" && FINE_TUNE_IDS.includes(window.location.hash.slice(1)),
  );

  useEffect(() => {
    const handleHashChange = () => {
      if (FINE_TUNE_IDS.includes(window.location.hash.slice(1))) {
        setOpen(true);
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const canAgree = settlingCanAgree(k, windowS);

  return (
    <>
      <SettingsRow
        id="transcription-live-settling"
        label="Refine live words"
        controlId="live-text-settling"
        description="Shows each word as it is heard and corrects the newest ones as more speech arrives, then fixes them. Off shows the live transcript a line at a time. On with a GPU also shows whole lines on a server without one; the heavy option refines words there too, at a high CPU and RAM cost. Takes effect when a recording starts or resumes."
      >
        <SelectBase
          id="live-text-settling"
          aria-describedby="live-text-settling-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={mode}
          onChange={(event) => void onPersist({ ...settings, live_text_settling: event.target.value as LiveTextSettlingMode })}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="off">Off</option>
          <option value="auto">On with a GPU</option>
          <option value="always">On, also without a GPU (heavy)</option>
        </SelectBase>
      </SettingsRow>

      <div className="settings-cell px-5 py-3 sm:px-6">
        <ButtonBase
          type="button"
          aria-expanded={open}
          aria-controls={FINE_TUNE_IDS.join(" ")}
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center gap-2 rounded-lg py-1 text-left text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
        >
          <ChevronRight
            className={cn("h-4 w-4 shrink-0 contrast-icon-muted transition-transform", open && "rotate-90")}
            aria-hidden="true"
          />
          Fine-tune
        </ButtonBase>
        {!open && !canAgree && (
          <p className="mt-1 pl-6 text-xs text-status-warning-fg">
            The current timing can fix words as heard, before they are checked again.
          </p>
        )}
      </div>

      {/* Rows directly in the card body, so the card's hairlines separate them. */}
      {open && (
        <>
          <SettingsRow
            id="transcription-live-settling-k"
            label="How soon a word is fixed"
            controlId="live-text-settling-k"
            description={`A word is fixed once it comes out the same ${k} more ${k === 1 ? "time" : "times"} in a row. Fewer checks fix words sooner; more wait for steadier wording.`}
          >
            <SelectBase
              id="live-text-settling-k"
              aria-describedby="live-text-settling-k-help"
              data-ui-fill="control"
              data-ui-disabled-fill="control-disabled"
              data-ui-border="full"
              value={k}
              onChange={(event) => void onPersist({ ...settings, live_text_settling_k: Number(event.target.value) })}
              disabled={!isAdmin}
              className={SETTINGS_SELECT_CLASS}
            >
              {Array.from({ length: 10 }, (_, index) => index + 1).map((value) => (
                <option key={value} value={value}>{checksLabel(value)}</option>
              ))}
            </SelectBase>
          </SettingsRow>
          <SettingsRow
            id="transcription-live-settling-window"
            label="Audio checked again"
            controlId="live-text-settling-window"
            description={
              <>
                <span className="block">How much recent audio is transcribed again on each check. A word older than this is fixed as it is.</span>
                <span className={cn("mt-1 block", !canAgree && "text-status-warning-fg")}>
                  {canAgree
                    ? "Some combinations fix every word before it can be checked again."
                    : `Too short for ${k} matching ${k === 1 ? "check" : "checks"}: words can be fixed as heard before they are checked again. Use more than ${SETTLING_STEP_S * (k + 1)} seconds, or fewer checks.`}
                </span>
              </>
            }
          >
            <SelectBase
              id="live-text-settling-window"
              aria-describedby="live-text-settling-window-help"
              data-ui-fill="control"
              data-ui-disabled-fill="control-disabled"
              data-ui-border="full"
              value={windowS}
              onChange={(event) => void onPersist({ ...settings, live_text_settling_window_s: Number(event.target.value) })}
              disabled={!isAdmin}
              className={SETTINGS_SELECT_CLASS}
            >
              {Array.from(new Set([2, 5, 10, 15, 20, 30, windowS])).sort((a, b) => a - b).map((value) => (
                <option key={value} value={value}>{value} seconds</option>
              ))}
            </SelectBase>
          </SettingsRow>
        </>
      )}
    </>
  );
}
