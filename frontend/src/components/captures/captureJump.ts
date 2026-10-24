/**
 * Scrolls the finished transcript so a capture's marker sits in the middle of
 * it, and moves focus to the marker's picture. Only the transcript's scroll
 * region moves, never the page around it. A marker always shows (captures do
 * not fold behind a count, as notes do), so it is found by its id.
 */
export function scrollTranscriptToCapture(captureId: number): void {
  const region = document.querySelector<HTMLElement>(
    '#transcript-view [data-testid="transcript-scroll-region"]',
  );
  const marker = region?.querySelector<HTMLElement>(
    `[data-testid="capture-marker"][data-capture-id="${captureId}"]`,
  );
  if (!region || !marker) return;
  const box = region.getBoundingClientRect();
  const rect = marker.getBoundingClientRect();
  const top = region.scrollTop + rect.top - box.top - (region.clientHeight - rect.height) / 2;
  region.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  marker.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
}
