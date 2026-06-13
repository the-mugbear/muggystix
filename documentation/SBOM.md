# Software Bill of Materials (SBOM)

> **Last verified against:** backend 2.201.0 / frontend 5.106.0 (2026-06-13)

BlueStick exposes its **live** dependency tree at `/reference/sbom` in the running application (route `frontend/src/pages/SbomReference.tsx`, backend `GET /api/v1/references/sbom`). That page reflects whatever the deployed build's `requirements.txt` and `frontend/package-lock.json` actually resolved to — there is no checked-in static list to drift.

## Why the live endpoint instead of a static doc

The previous static table aged the moment any dependency was added or bumped. A live SBOM:

- Reads `requirements.txt` via `importlib.metadata` so the version reported is the **installed** version (resolves the real wheel that pip chose, including any constraints satisfied by transitive pins).
- Reads `frontend/package-lock.json` directly so transitive npm deps are accurate to the lockfile.
- Classifies each component as **direct** (listed in `requirements.txt` / `package.json` root) or **transitive** (resolved as a sub-dependency).
- Includes license info (PEP 639 expression or classifier fallback for Python; lockfile-declared license for npm).
- Memoised by manifest mtimes + the app's release version, so a `platform_version.json` bump invalidates the cache even when the manifests themselves are unchanged.

## How to view the SBOM

- **In-app:** click **Reference → Software Bill of Materials**. The page supports search, a Backend / Frontend / All segment, a Direct-only toggle, paging up to 250 rows per page, and a "Download JSON" button if you need an offline snapshot.
- **Raw API:** `curl -k https://<host>/api/v1/references/sbom` returns the structured JSON envelope (`{app_version, generated_at, summary, components[]}`).

## Important caveat (the page repeats this)

> **Presence confirms a package is bundled. It does NOT confirm a vulnerability is exploitable.**

The SBOM page exists to answer "is package X in this build, where does it live, and did we choose it or did something else pull it in?" — not "are we affected by CVE-Y?". Use a downstream vulnerability scanner against the JSON output if you need that answer.

## Backend module

See `backend/app/services/sbom_service.py` for the implementation. Tests in `backend/tests/test_phase1_regressions.py::test_sbom_cache_invalidates_on_app_version_change` pin the cache-key invariant.
