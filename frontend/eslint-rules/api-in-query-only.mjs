// An API function is called only inside a `queryFn` or a `mutationFn`
// (src/lib/query.ts; UI_STYLE_GUIDE §48).
//
// "An API function" is anything imported from the `services/api` barrel and
// then CALLED — a named export (`listThings(...)`) or the default client
// (`api.get(...)`).  Imports that are not requests (URL builders, constants,
// the current project id) are named in `allow`.
//
// A call is in the right place when, walking outwards, it sits inside:
//   - the value of a property named `queryFn` or `mutationFn`;
//   - a function passed to `useListQuery` / `usePagedList` (their fetcher IS
//     the queryFn);
//   - the value of a property or JSX attribute whose name ends in `Fn`
//     (`uploadFn={(file) => uploadThing(id, file)}`): a function handed to a
//     component that runs it inside its own mutation;
//   - a named local helper (`const putUser = async (body) => api.put(…)`)
//     whose every use is itself in one of those places.
//
// Everything else — an effect, an event handler, a hand-made `load()` — is the
// old way of talking to the server and is refused.
const CONTAINER = /^(queryFn|mutationFn)$/;
const HANDED_ON = /Fn$/;
const LIST_HOOKS = new Set(['useListQuery', 'usePagedList']);
const CLIENT_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'request']);

const nameOf = (key) => (key && (key.type === 'Identifier' || key.type === 'JSXIdentifier') ? key.name
  : key && key.type === 'Literal' ? String(key.value) : null);

function inQueryOrMutation(node) {
  let child = node;
  for (let parent = node.parent; parent; child = parent, parent = parent.parent) {
    if (parent.type === 'Property' && parent.value === child) {
      const name = nameOf(parent.key);
      if (name && (CONTAINER.test(name) || HANDED_ON.test(name))) return true;
    }
    if (parent.type === 'JSXAttribute') {
      const name = nameOf(parent.name);
      if (name && HANDED_ON.test(name)) return true;
    }
    if (parent.type === 'CallExpression' && parent.arguments.includes(child)
      && parent.callee.type === 'Identifier' && LIST_HOOKS.has(parent.callee.name)) return true;
  }
  return false;
}

export default {
  meta: {
    type: 'problem',
    docs: { description: 'API functions are called only inside a queryFn or a mutationFn.' },
    schema: [{
      type: 'object',
      properties: { allow: { type: 'array', items: { type: 'string' } } },
      additionalProperties: false,
    }],
    messages: {
      outside: "'{{name}}' talks to the server: call it inside a queryFn (useQuery) or a mutationFn (useMutation) — see src/lib/query.ts.",
    },
  },
  create(context) {
    const allow = new Set(context.options[0]?.allow ?? []);
    const named = new Set();
    let client = null;
    // A call that is not lexically inside a query / mutation, with the local
    // function it sits in (if that function has a name).  Judged at the end:
    // a named local helper is fine when EVERY use of its name is itself in
    // the right place — `mutationFn: putUser`, `queryFn: () => answer(q)`.
    const pending = [];

    /** The variable of the nearest enclosing function that has a name
     *  (`function f() {}`, `const f = () => {}`), or null. */
    const carrierOf = (node) => {
      for (let fn = node.parent; fn; fn = fn.parent) {
        const isFn = fn.type === 'FunctionDeclaration' || fn.type === 'FunctionExpression'
          || fn.type === 'ArrowFunctionExpression';
        if (!isFn) continue;
        const declaration = fn.type === 'FunctionDeclaration' ? fn
          : fn.parent && fn.parent.type === 'VariableDeclarator' && fn.parent.init === fn ? fn.parent : null;
        if (!declaration) return null;   // an anonymous function: judged where it stands
        const [variable] = context.sourceCode.getDeclaredVariables(declaration);
        return variable ?? null;
      }
      return null;
    };
    const usedOnlyInPlace = (variable) => {
      const uses = variable.references.filter((ref) => !ref.init);
      return uses.length > 0 && uses.every((ref) => inQueryOrMutation(ref.identifier));
    };

    return {
      'Program:exit'() {
        for (const { node, name, carrier } of pending) {
          if (carrier && usedOnlyInPlace(carrier)) continue;
          context.report({ node, messageId: 'outside', data: { name } });
        }
      },
      ImportDeclaration(node) {
        if (node.importKind === 'type') return;
        if (!/(^|\/)services\/api$/.test(String(node.source.value))) return;
        for (const spec of node.specifiers) {
          if (spec.type === 'ImportDefaultSpecifier') client = spec.local.name;
          else if (spec.type === 'ImportSpecifier' && spec.importKind !== 'type' && !allow.has(spec.imported.name)) {
            named.add(spec.local.name);
          }
        }
      },
      CallExpression(node) {
        const { callee } = node;
        let name = null;
        if (callee.type === 'Identifier' && named.has(callee.name)) name = callee.name;
        else if (callee.type === 'MemberExpression' && callee.object.type === 'Identifier'
          && callee.object.name === client && callee.property.type === 'Identifier'
          && CLIENT_METHODS.has(callee.property.name)) name = `${client}.${callee.property.name}`;
        if (!name || inQueryOrMutation(node)) return;
        pending.push({ node, name, carrier: carrierOf(node) });
      },
    };
  },
};
