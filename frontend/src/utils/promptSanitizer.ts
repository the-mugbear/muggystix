/**
 * Redact secret material from agent-instruction prompts before they are
 * handed to a third-party LLM provider.
 *
 * The 2026-04-15 code review (critical finding #1) flagged that
 * `InAppAgentPanel` was POSTing the raw ``instructions`` markdown
 * emitted by ``/test-plans/{id}/execute`` and
 * ``/test-plans/generate-recon`` directly to the configured LLM
 * provider.  Those instruction blocks include:
 *
 *   - The one-time ``X-API-Key: nm_agent_...`` header (so the agent
 *     can authenticate as the user to BlueStick's own API).
 *   - Inlined scanner integration credentials (Nessus/OpenVAS/Nuclei/Burp
 *     access + secret keys from ``integration_service.decrypt_integration``).
 *
 * Piping these through an LLM turns every configured provider into a
 * potential credential sink — the provider logs the request body, the
 * operator can see it, and any prompt caching at the provider retains
 * it indefinitely.
 *
 * The fix is to sanitize on the client side before the request ever
 * leaves the browser.  The full (unredacted) instructions are still
 * available for the user to copy into a terminal-side agent (Claude
 * Code, Codex, etc) — only the in-app LLM path gets the redacted
 * version.  The redaction is non-reversible and the placeholder text
 * tells the agent explicitly that secrets were stripped and must be
 * requested from the operator out-of-band.
 */

const REDACTED = '[REDACTED — request from operator out-of-band]';

/**
 * Strip secrets from an agent-instruction prompt.
 *
 * Patterns removed:
 *  1. ``X-API-Key: nm_agent_...`` — the BlueStick agent API token
 *     the user was just handed.  The LLM doesn't need it; approval
 *     still happens in the user's terminal / in-app UI, and the
 *     coordinator model forbids autonomous tool execution anyway.
 *  2. Markdown bullet lines that inline scanner credentials emitted
 *     by ``_integration_block`` in ``agent_prompt_service.py``:
 *     lines matching ``  - Access key:``, ``  - Secret key:``,
 *     ``  - Password:``, ``  - Username:``, ``  - API key:``,
 *     ``  - PDCP token:``, ``  - Secret:``.  The label stays, the
 *     value becomes REDACTED.
 *  3. Bare ``nm_agent_xxx`` tokens anywhere in the text (defense in
 *     depth against the key appearing outside the header block).
 *
 * The function is intentionally conservative: if a caller's prompt
 * contains content that looks like a key pattern, it gets redacted.
 * False positives are cheap (the LLM sees ``[REDACTED]`` instead of a
 * non-secret string); false negatives are expensive (the key leaks).
 */
export function sanitizePromptForLlm(prompt: string): string {
  if (!prompt) return prompt;
  let out = prompt;

  // 1. Strip the X-API-Key line.  Match the whole line so the fence
  //    formatting stays intact.
  out = out.replace(
    /^\s*X-API-Key:\s*nm_agent_[A-Za-z0-9_-]+\s*$/gm,
    `X-API-Key: ${REDACTED}`,
  );

  // 2. Strip inlined integration credential bullets.  The prompt
  //    template uses ``  - <Label>: `<value>``` markdown, so we match
  //    the label and replace everything after the colon on that line.
  const credLabels = [
    'Access key',
    'Secret key',
    'Password',
    'Username',
    'API key',
    'PDCP token',
    'Secret',
  ];
  for (const label of credLabels) {
    const re = new RegExp(
      String.raw`^(\s*-\s*${label}:\s*)\`[^\`]*\`\s*$`,
      'gim',
    );
    out = out.replace(re, `$1\`${REDACTED}\``);
  }

  // 3. Defense-in-depth: catch any bare agent-key tokens elsewhere in
  //    the text (e.g. if a future template references the key outside
  //    the header block).  Width matches the X-API-Key matcher above
  //    (``+``, not ``{20,}``) so a short-suffix token inlined in prose
  //    is caught by the backstop too — the ``nm_agent_`` prefix plus a
  //    word boundary is distinctive enough to avoid false positives.
  out = out.replace(/nm_agent_[A-Za-z0-9_-]+/g, REDACTED);

  return out;
}
