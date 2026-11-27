import { createElement } from "react";

import legalTerms from "@/lib/legalTerms.json";

/**
 * The terms of use as the app shows them: on the owner's setup step and on the
 * notice everyone else accepts (#1004). Both render this, from
 * `legalTerms.json`, and `scripts/validate_docs.py` fails when docs/LEGAL.md
 * says anything different, so the three cannot drift apart.
 */
type LegalTermsBlock =
  | { paragraph: string; list?: undefined }
  | { list: string[]; paragraph?: undefined };

interface LegalTermsContent {
  sections: { heading: string; blocks: LegalTermsBlock[] }[];
  /** The sentence that closes the terms, which accepting agrees to. */
  closing: string;
  /** Shown to the owner alone, on the setup step. */
  ownerResponsibility: string;
}

export const LEGAL_TERMS: LegalTermsContent = legalTerms;

export default function LegalTerms({
  headingLevel,
}: {
  /** The level of each section's heading, one below the page's own title. */
  headingLevel: 2 | 3;
}) {
  return (
    // Not its own scroll region. A nested scroller inside a page that also
    // scrolls traps the wheel, and these terms are short enough to flow.
    <div className="prose prose-sm dark:prose-invert max-w-none bg-surface-inset p-4 rounded-lg border border-surface-border">
      {LEGAL_TERMS.sections.map((section, index) => (
        <section key={section.heading}>
          {createElement(
            `h${headingLevel}`,
            { className: `text-base font-semibold ${index === 0 ? "mt-0" : ""}` },
            `${index + 1}. ${section.heading}`,
          )}
          {section.blocks.map((block) =>
            block.list ? (
              <ul key={block.list[0]} className="list-disc pl-4 space-y-1">
                {block.list.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p key={block.paragraph}>{block.paragraph}</p>
            ),
          )}
        </section>
      ))}
      <p className="font-medium mt-4">{LEGAL_TERMS.closing}</p>
    </div>
  );
}
