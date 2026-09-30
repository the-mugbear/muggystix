/**
 * Proposal decisions announce themselves (v5.316.1) so the top bar's pending
 * count follows at once instead of on its next minute's poll.
 */
export const PROPOSALS_CHANGED_EVENT = 'nm:proposals-changed';

export const announceProposalsChanged = (): void => {
  window.dispatchEvent(new CustomEvent(PROPOSALS_CHANGED_EVENT));
};

/** The client's name, not its whole User-Agent: an MCP client names itself
 *  ("claude-code 2.1.0"), but a browser or library UA runs to a line. */
export const shortClient = (client: string): string => {
  const head = client.split(' (')[0].trim();
  if (/^Mozilla\//.test(head)) return 'a browser';
  return head.length > 40 ? `${head.slice(0, 39)}…` : head;
};
