import { useEffect, useState } from "react";

import {
  getSpeakerFrameAvailability,
  type SpeakerFrameAvailability,
} from "@/lib/api/speakerFrames";

/**
 * Whether this account may tick Screen study at Start Meeting (#653): only
 * while the installation has `speaker_frame_sampling` set to `owner`, and
 * only for the primary Owner. One request per page load, asked again after
 * five minutes so a change on the server shows without a reload.
 */

const AVAILABILITY_TTL_MS = 5 * 60_000;

let cached: { value: SpeakerFrameAvailability | null; at: number } | null = null;

let inflight: Promise<SpeakerFrameAvailability | null> | null = null;

export const loadScreenStudyAvailability = (
  now: number = Date.now(),
): Promise<SpeakerFrameAvailability | null> => {
  if (cached && now - cached.at < AVAILABILITY_TTL_MS) {
    return Promise.resolve(cached.value);
  }
  if (!inflight) {
    inflight = getSpeakerFrameAvailability()
      .then((value) => {
        cached = { value, at: Date.now() };
        return value;
      })
      .catch(() => {
        // Unknown means no checkbox; the next mount asks again.
        return null;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
};

/** Test seam: forget what the last request answered. */
export const resetScreenStudyAvailability = () => {
  cached = null;
  inflight = null;
};

export const useScreenStudyAvailability = (
  enabled: boolean,
): SpeakerFrameAvailability | null => {
  const [value, setValue] = useState<SpeakerFrameAvailability | null>(
    () => cached?.value ?? null,
  );
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let live = true;
    void loadScreenStudyAvailability().then((next) => {
      if (live) {
        setValue(next);
      }
    });
    return () => {
      live = false;
    };
  }, [enabled]);
  return enabled ? value : null;
};
