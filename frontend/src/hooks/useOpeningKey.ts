import { useState } from 'react';

/**
 * A key that changes each time a dialog or panel OPENS — for one whose every
 * opening starts clean:
 *
 *   const AddThingDialog = (props) => <Body key={useOpeningKey(props.open)} {...props} />;
 *
 * The body's state is then simply its initial state: a draft, a selection, a
 * request key, a mutation's failure.  (The other way — an effect on `open`
 * that sets each piece back — shows the previous opening's state for one
 * render, has to name every piece, and forgets the one added later.)
 *
 * `subject` is what the opening is OF: `open` itself, or the record a panel
 * was opened for (`test?.id ?? null`), so that another record is a new
 * opening too.  The key does not change when the subject is cleared: the body
 * stays as it was while it closes.
 *
 * Not for a dialog that keeps something across openings on purpose (the
 * export dialogs keep the chosen format, the report draft its instructions).
 */
export function useOpeningKey(subject: unknown): number {
  const [seen, setSeen] = useState({ key: 0, subject });
  if (Object.is(seen.subject, subject)) return seen.key;
  const closed = subject == null || subject === false;
  const next = { key: closed ? seen.key : seen.key + 1, subject };
  setSeen(next);
  return next.key;
}

export default useOpeningKey;
