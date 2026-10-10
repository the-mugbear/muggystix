/**
 * What an OLDER review concluded — for display only.
 *
 * Marking a host Reviewed used to ask for a conclusion.  It is one click now
 * (owner decision, 2026-10-10) and nothing records one, but rows that carry a
 * conclusion keep it, and a host shows it for as long as that review stands.
 * `no_action` is older still and exists in stored data.  There is no list of
 * choices here on purpose: nothing offers them.
 */
const STORED_REVIEW_CONCLUSION_LABEL: Record<string, string> = {
  no_issue: 'No actionable issue',
  no_action: 'No action needed',
  finding_created: 'Finding created',
  needs_evidence: 'Needs more evidence',
  out_of_scope: 'Out of scope',
  duplicate: 'Duplicate asset',
};

/** The words for a stored conclusion; an unknown value is shown as stored. */
export const storedReviewConclusionLabel = (value: string | null | undefined): string | null =>
  (value ? STORED_REVIEW_CONCLUSION_LABEL[value] ?? value : null);
