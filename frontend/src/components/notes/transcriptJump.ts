/**
 * Scrolls the active transcript so a body note sits in the middle of it.
 *
 * The target is the note's own line, else its marked words (a selection note
 * folded behind its line's count), else the last shown line at or before the
 * one the note follows (`afterIndex`, from placement), which is where a folded
 * point note's count sits. A note before every line scrolls to the top.
 * Only the transcript's scroll region moves, never the page around it.
 */
export function scrollTranscriptToNote(noteId: string, afterIndex: number): void {
  const region = document.querySelector<HTMLElement>(
    '#transcript-view [data-testid="transcript-scroll-region"], #panel-transcript [data-testid="live-transcript-scroll"]',
  );
  if (!region) return;
  const id = CSS.escape(noteId);
  const target =
    region.querySelector<HTMLElement>(`[data-testid="timeline-note"][data-note-id="${id}"]`) ??
    region.querySelector<HTMLElement>(`mark[data-note-id="${id}"]`) ??
    lineAtOrBefore(region, afterIndex);
  if (!target) {
    region.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  const box = region.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  const top = region.scrollTop + rect.top - box.top - (region.clientHeight - rect.height) / 2;
  region.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

/** The shown line with the largest list index at or before `afterIndex`. */
const lineAtOrBefore = (region: HTMLElement, afterIndex: number): HTMLElement | null => {
  let best: HTMLElement | null = null;
  let bestIndex = -1;
  for (const row of region.querySelectorAll<HTMLElement>("[data-order-index]")) {
    const index = Number(row.dataset.orderIndex);
    if (index <= afterIndex && index > bestIndex) {
      best = row;
      bestIndex = index;
    }
  }
  return best;
};
