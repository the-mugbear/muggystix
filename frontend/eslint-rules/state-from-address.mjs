// A filter in the address has ONE owner, the address (UI_STYLE_GUIDE §39).
//
// State seeded from it — `useState(searchParams.get('q'))`, usually with an
// effect writing the state back — is a second owner: Back, Forward or a link
// to the same page changes the address, the state keeps the old value, and
// the effect then writes the old value over the address the reader had just
// gone to.  Seven pages had this until 5.354.0 / 5.358.0, and an eighth
// (Ingestion Results) until 5.361.0.
//
// Read the filter from the address on every render and change it by writing
// the address; a search box is `hooks/useUrlSearchDraft` (only the text being
// typed is state).
//
// Refused: a `<something>Params.get / getAll / has(...)` call inside the
// argument of `useState(...)` — passed directly, from an initialiser, or
// through a `const` declared from one:
//
//   const urlSearch = searchParams.get('search') ?? '';
//   const [text, setText] = useState(urlSearch);        // the eighth page
//
// (Until 5.361.0 this was a `no-restricted-syntax` selector, which cannot
// follow a variable.)
const READS = /^(get|getAll|has)$/;
const PARAMS = /[pP]arams$/;

const isAddressRead = (node) => node.type === 'CallExpression'
  && node.callee.type === 'MemberExpression'
  && node.callee.property.type === 'Identifier' && READS.test(node.callee.property.name)
  && node.callee.object.type === 'Identifier' && PARAMS.test(node.callee.object.name);

export const stateFromAddress = {
  meta: {
    type: 'problem',
    docs: { description: 'State is not seeded from the address.' },
    schema: [],
    messages: {
      seeded: 'Do not seed state from the address: read the filter from it on every render '
        + '(a search box is hooks/useUrlSearchDraft). See UI_STYLE_GUIDE §39.',
      seededVia: "Do not seed state from the address: '{{name}}' is read from it. Read the filter "
        + 'from the address on every render (a search box is hooks/useUrlSearchDraft). See UI_STYLE_GUIDE §39.',
    },
  },
  create(context) {
    const { sourceCode } = context;

    /** Every node under `root`, itself included. */
    function* walk(root) {
      yield root;
      for (const key of sourceCode.visitorKeys[root.type] ?? []) {
        const child = root[key];
        if (Array.isArray(child)) {
          for (const item of child) if (item && typeof item.type === 'string') yield* walk(item);
        } else if (child && typeof child.type === 'string') {
          yield* walk(child);
        }
      }
    }
    const firstAddressRead = (root) => {
      for (const node of walk(root)) if (isAddressRead(node)) return node;
      return null;
    };

    /** The `const` this identifier names, when it is declared from the address. */
    const declaredFromAddress = (identifier) => {
      for (let scope = sourceCode.getScope(identifier); scope; scope = scope.upper) {
        const variable = scope.set.get(identifier.name);
        if (!variable) continue;
        const [def] = variable.defs;
        if (variable.defs.length !== 1 || def.type !== 'Variable') return false;
        if (def.parent?.kind !== 'const' || def.node.id.type !== 'Identifier' || !def.node.init) return false;
        return firstAddressRead(def.node.init) !== null;
      }
      return false;
    };

    return {
      "CallExpression[callee.name='useState']"(call) {
        for (const argument of call.arguments) {
          const read = firstAddressRead(argument);
          if (read) {
            context.report({ node: read, messageId: 'seeded' });
            continue;
          }
          for (const node of walk(argument)) {
            if (node.type !== 'Identifier') continue;
            // A property name is not a reference to a variable.
            const { parent } = node;
            if (parent.type === 'MemberExpression' && parent.property === node && !parent.computed) continue;
            if (parent.type === 'Property' && parent.key === node && !parent.computed && !parent.shorthand) continue;
            if (declaredFromAddress(node)) {
              context.report({ node, messageId: 'seededVia', data: { name: node.name } });
              break;
            }
          }
        }
      },
    };
  },
};

export default stateFromAddress;
