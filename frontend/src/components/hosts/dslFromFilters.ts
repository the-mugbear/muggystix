/**
 * DSL value quoting for the command-bar autocomplete.
 *
 * The panel → query serializer that lived here ("Convert filters → query") was
 * removed in 5.248.2: it could not be made faithful.  The panel ORs the
 * selected severities and matches port/service/state on ONE port row; the
 * serialized query ANDed the severities and matched each port clause
 * independently, so a conversion silently changed the result.  The query and
 * the structured filters are sent together instead — the API always took both.
 */

// A value is "bare" (needs no quoting) only if it contains none of the DSL's
// structural characters. ':' must NOT be here — the lexer breaks tokens on it,
// so an unquoted IPv6 (fe80::1) or URL-shaped value would split mid-value.
const BARE = /^[A-Za-z0-9_./@-]+$/;

/** Quote a DSL value if it isn't a bare token (spaces, commas, quotes, parens
 *  would otherwise reparse as separate clauses/operators). */
export function quote(value: string): string {
  if (BARE.test(value)) return value;
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}
