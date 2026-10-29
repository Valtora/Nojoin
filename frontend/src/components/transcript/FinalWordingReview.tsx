import type { TranscriptSegment } from "@/types";

/** Final wording offered once, without replacing or duplicating protected speech. */
export default function FinalWordingReview({ segment }: { segment: TranscriptSegment }) {
  if (!segment.final_review?.text) return null;
  return (
    <details className="mt-2 rounded border border-status-info-border bg-status-info-bg p-2 text-sm text-status-info-fg">
      <summary className="cursor-pointer">Review final wording</summary>
      <div className="mt-2 whitespace-pre-wrap">
        <p>Your wording has been kept because the final pass could not match it confidently.</p>
        <blockquote className="mt-1">{segment.final_review.text}</blockquote>
      </div>
    </details>
  );
}
