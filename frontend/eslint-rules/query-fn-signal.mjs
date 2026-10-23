// A `queryFn` that calls an API function hands on the query's `signal`
// (.claude/rules/frontend.md, "Fetching lists and details"): a GET function in
// `services/api` takes `signal?: AbortSignal`, last or in its options object,
// and a read the query has given up on (a new key, an unmount) is then
// cancelled instead of answering into nothing.
//
// "An API function" is anything imported from the `services/api` barrel and
// then CALLED — a named export or the default client (`api.get(…)`).  Names in
// `allow` are not requests (URL builders, the current project id); names in
// `noSignal` are requests that take no signal (a click-only download).
//
// A query function is the value of a property named `queryFn`, or the fetcher
// handed to `useListQuery` / `usePagedList` (both call it with `{ signal }`).
// Its signal is a parameter named `signal`, a `{ signal }` destructured from a
// parameter (under any local name), or `<param>.signal`.  The call hands it on
// when one of its arguments is that signal, or an object literal with a
// property whose value is it (`{ offset, signal }`).
//
// Only calls written inside the query function are judged: a named helper the
// queryFn calls is its own function, and passes the signal it is given.
const LIST_HOOKS = new Set(['useListQuery', 'usePagedList']);
const CLIENT_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'request']);

const isFunction = (node) => node.type === 'ArrowFunctionExpression' || node.type === 'FunctionExpression';
const keyName = (key) => (key && key.type === 'Identifier' ? key.name
  : key && key.type === 'Literal' ? String(key.value) : null);

/** Is `fn` a query function — a `queryFn:` value or a list hook's fetcher? */
function isQueryFunction(fn) {
  const parent = fn.parent;
  if (!parent) return false;
  if (parent.type === 'Property' && parent.value === fn && keyName(parent.key) === 'queryFn') return true;
  return parent.type === 'CallExpression' && parent.arguments.includes(fn)
    && parent.callee.type === 'Identifier' && LIST_HOOKS.has(parent.callee.name);
}

/** The ways `fn`'s parameters name the signal: bare names, and the parameter
 *  that holds it (`<name>.signal`) — only the FIRST, and only when no
 *  parameter is the signal itself (a job poll's `queryFn(job, signal)`). */
function signalOf(fn) {
  const names = new Set();
  const holders = new Set();
  fn.params.forEach((param, index) => {
    const target = param.type === 'AssignmentPattern' ? param.left : param;
    if (target.type === 'Identifier') {
      if (target.name === 'signal') names.add(target.name);
      else if (index === 0) holders.add(target.name);
    } else if (target.type === 'ObjectPattern') {
      for (const prop of target.properties) {
        if (prop.type !== 'Property' || keyName(prop.key) !== 'signal') continue;
        const value = prop.value.type === 'AssignmentPattern' ? prop.value.left : prop.value;
        if (value.type === 'Identifier') names.add(value.name);
      }
    }
  });
  if (names.size) holders.clear();
  return { names, holders };
}

function isSignal(expr, { names, holders }) {
  if (!expr) return false;
  if (expr.type === 'Identifier') return names.has(expr.name);
  return expr.type === 'MemberExpression' && !expr.computed
    && expr.object.type === 'Identifier' && holders.has(expr.object.name)
    && expr.property.type === 'Identifier' && expr.property.name === 'signal';
}

/** Does this argument carry the signal: the signal itself, the parameter that
 *  holds it (a list fetcher's `req`, handed on whole), an object literal with
 *  a property carrying it, or a choice whose both branches carry it? */
function carries(expr, signal) {
  if (isSignal(expr, signal)) return true;
  if (expr.type === 'Identifier') return signal.holders.has(expr.name);
  if (expr.type === 'ObjectExpression') {
    return expr.properties.some((prop) => prop.type === 'Property' && carries(prop.value, signal));
  }
  if (expr.type === 'ConditionalExpression') {
    return carries(expr.consequent, signal) && carries(expr.alternate, signal);
  }
  return false;
}

const handsOn = (call, signal) => call.arguments.some((arg) => carries(arg, signal));

export default {
  meta: {
    type: 'problem',
    docs: { description: 'A queryFn that calls an API function hands on the query\'s signal.' },
    schema: [{
      type: 'object',
      properties: {
        allow: { type: 'array', items: { type: 'string' } },
        noSignal: { type: 'array', items: { type: 'string' } },
      },
      additionalProperties: false,
    }],
    messages: {
      missing: "'{{name}}' is called in a queryFn without the query's signal: pass `signal` "
        + '(last, or in its options object) so a read the query gave up on is cancelled.',
    },
  },
  create(context) {
    const options = context.options[0] ?? {};
    const skip = new Set([...(options.allow ?? []), ...(options.noSignal ?? [])]);
    const named = new Set();
    let client = null;
    return {
      ImportDeclaration(node) {
        if (node.importKind === 'type') return;
        if (!/(^|\/)services\/api$/.test(String(node.source.value))) return;
        for (const spec of node.specifiers) {
          if (spec.type === 'ImportDefaultSpecifier') client = spec.local.name;
          else if (spec.type === 'ImportSpecifier' && spec.importKind !== 'type' && !skip.has(spec.imported.name)) {
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
        if (!name) return;
        let fn = node.parent;
        while (fn && !(isFunction(fn) && isQueryFunction(fn))) fn = fn.parent;
        if (!fn) return;
        if (!handsOn(node, signalOf(fn))) context.report({ node, messageId: 'missing', data: { name } });
      },
    };
  },
};
