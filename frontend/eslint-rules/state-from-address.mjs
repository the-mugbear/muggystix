// A filter in the address has ONE owner, the address (UI_STYLE_GUIDE §39).
//
// State seeded from it — `useState(searchParams.get('q'))`, usually with an
// effect writing the state back — is a second owner: Back, Forward or a link
// to the same page changes the address, the state keeps the old value, and
// the effect then writes the old value over the address the reader had just
// gone to.  Seven pages had this until 5.354.0 / 5.358.0.
//
// Read the filter from the address on every render and change it by writing
// the address; a search box is `hooks/useUrlSearchDraft` (only the text being
// typed is state).  This is a `no-restricted-syntax` entry, not a rule of its
// own: any `<something>Params.get / getAll / has(...)` call inside the
// argument of `useState(...)`, whether passed directly or from an initialiser.
export const STATE_FROM_ADDRESS = {
  selector: "CallExpression[callee.name='useState'] "
    + "CallExpression[callee.property.name=/^(get|getAll|has)$/][callee.object.name=/[pP]arams$/]",
  message: 'Do not seed state from the address: read the filter from it on every render '
    + '(a search box is hooks/useUrlSearchDraft). See UI_STYLE_GUIDE §39.',
};

export default STATE_FROM_ADDRESS;
