/**
 * A Hosts-page selection handed to the operator's agent (v5.221.0; design
 * review item 6). The selection is a FIXED id list resolved at the moment of
 * the click — not the query that produced it — and the agent is handed the
 * ids directly (agentInstruction.proposeTests).
 */

/**
 * One line describing how a bulk-bar selection was made, shown beside the
 * count.  Filter values come from the Hosts page query context.
 */
export const describeSelection = (
  count: number,
  allMatching: boolean,
  queryContext: Record<string, string | boolean | number | string[] | undefined>,
): string => {
  if (!allMatching) return `${count} host${count === 1 ? '' : 's'} checked on the Hosts page`;
  const filters = Object.entries(queryContext)
    .filter(([, v]) => v !== undefined && v !== '' && v !== false && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(',') : String(v)}`);
  const via = filters.length ? ` matching ${filters.join(' ')}` : ' in the project';
  return `all ${count} hosts${via}, resolved to a fixed list`;
};
