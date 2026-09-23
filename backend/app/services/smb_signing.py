"""The stored SMB-signing posture of a host (``Host.smb_signing``), v2.387.0.

Three states, the same whichever tool observed them:

* ``required``     — the server requires signing (nmap "enabled and required",
  smb-security-mode "message_signing: required", NetExec ``(signing:True)``).
* ``not_required`` — signing is available but not required (nmap "enabled but
  not required", smb-security-mode "supported", NetExec ``(signing:False)``).
* ``disabled``     — signing is off (smb-security-mode "message_signing:
  disabled", SMBv1).

Before, nmap wrote ``enabled`` for "not required" and NetExec wrote
``enabled`` for "required" and ``disabled`` for "not required": the same host
flipped with the import order, and the relay condition (``disabled`` only)
missed every host nmap had seen.  A host is open to NTLM relay unless signing
is required, so the relay condition is ``RELAYABLE``.
"""
REQUIRED = "required"
NOT_REQUIRED = "not_required"
DISABLED = "disabled"

RELAYABLE = (NOT_REQUIRED, DISABLED)
