/** Browser-level fake: listeners and properties follow native EventSource. */
export class TestTranscriptEventSource extends EventTarget {
  static instances: TestTranscriptEventSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string, readonly options?: EventSourceInit) {
    super();
    TestTranscriptEventSource.instances.push(this);
  }

  open(): void { this.onopen?.(new Event("open")); }
  fail(): void { this.onerror?.(new Event("error")); }
  close(): void { this.closed = true; }
  hint(sequence: number): void {
    this.dispatchEvent(new MessageEvent("transcript.changed", { data: JSON.stringify({ sequence }) }));
  }
}
