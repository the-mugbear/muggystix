-- bluestick:upgrade-refusals
--
-- The ONE list of the rows a migration REFUSES to change (the revision raises,
-- nothing is changed, and the new backend does not start).  Read by:
--   * deploy.sh option 1, against the OLD database before `$DC build`: any
--     non-zero `refused`, or a check that could not run, stops the deploy
--     (DEPLOY_SKIP_REFUSAL_CHECK=1 overrides);
--   * collect-logs.sh ("Upgrade refusals") and migration-failure-report.sh
--     (section 8), which print it.
-- They used to carry their own copies of these queries, and the copies
-- drifted (review 2026-10-22, B2-1).
--
-- The contract every statement keeps (deploy.sh parses the unaligned output):
--   * ONE statement per line, starting with SELECT, returning exactly ONE row
--     of three columns: revision | refused | detail;
--   * `refused` is the count the revision refuses on — it must be 0;
--   * `detail` is counts only, never a row's value (the bundle is shared);
--   * plain SQL that reads the same on the schema before AND after the
--     revision, so a database already past it answers 0.
-- A new refusing revision adds its line here, and nowhere else
-- (tests/test_migration_review_2026_10_10_schema.py pins this file).

-- b2e5a8c1d4f6: one scanner finding per issue (`row:` keys identify one row, not an issue).
SELECT 'b2e5a8c1d4f6 issues with more than one scanner finding' AS revision, count(*) AS refused, 'findings involved: ' || coalesce(sum(n), 0) AS detail FROM (SELECT count(*) AS n FROM findings WHERE source = 'scanner' AND dedup_key IS NOT NULL AND substr(dedup_key, 1, 4) <> 'row:' GROUP BY project_id, dedup_key HAVING count(*) > 1) d;

-- e1b4c7d0a2f5: one scope per project.  Several scopes in a project are MERGED, not refused.
SELECT 'e1b4c7d0a2f5 scopes with no project, and entries held by two scopes of one project' AS revision, (SELECT count(*) FROM scopes WHERE project_id IS NULL) + (SELECT count(*) FROM (SELECT 1 FROM subnets sn JOIN scopes s ON s.id = sn.scope_id GROUP BY s.project_id, sn.cidr HAVING count(DISTINCT sn.scope_id) > 1) c) + (SELECT count(*) FROM (SELECT 1 FROM scope_domains d JOIN scopes s ON s.id = d.scope_id GROUP BY s.project_id, d.domain HAVING count(DISTINCT d.scope_id) > 1) c) AS refused, 'scopes without project: ' || (SELECT count(*) FROM scopes WHERE project_id IS NULL) || ', cidrs in two scopes: ' || (SELECT count(*) FROM (SELECT 1 FROM subnets sn JOIN scopes s ON s.id = sn.scope_id GROUP BY s.project_id, sn.cidr HAVING count(DISTINCT sn.scope_id) > 1) c) || ', domains in two scopes: ' || (SELECT count(*) FROM (SELECT 1 FROM scope_domains d JOIN scopes s ON s.id = d.scope_id GROUP BY s.project_id, d.domain HAVING count(DISTINCT d.scope_id) > 1) c) || '; projects with several scopes (merged, not refused): ' || (SELECT count(*) FROM (SELECT 1 FROM scopes WHERE project_id IS NOT NULL GROUP BY project_id HAVING count(*) > 1) p) AS detail;

-- a4f8c2e6b0d1: a CIDR once in a scope.  Copies are MERGED; those whose copies differ in description or site are refused.
SELECT 'a4f8c2e6b0d1 one CIDR twice in a scope with copies that differ in description or site' AS revision, count(*) FILTER (WHERE descriptions > 1 OR sites > 1 OR site_ids > 1) AS refused, 'duplicated cidrs (merged): ' || count(*) AS detail FROM (SELECT count(DISTINCT nullif(btrim(description), '')) AS descriptions, count(DISTINCT nullif(btrim(site), '')) AS sites, count(DISTINCT site_id) AS site_ids FROM subnets GROUP BY scope_id, cidr HAVING count(*) > 1) d;

-- e3b7a1c9d5f0: every row has a project.
SELECT 'e3b7a1c9d5f0 rows with no project' AS revision, h + s + j + p + r AS refused, 'hosts: ' || h || ', scans: ' || s || ', ingestion jobs: ' || j || ', parse errors: ' || p || ', dns records: ' || r AS detail FROM (SELECT (SELECT count(*) FROM hosts_v2 WHERE project_id IS NULL) AS h, (SELECT count(*) FROM scans WHERE project_id IS NULL) AS s, (SELECT count(*) FROM ingestion_jobs WHERE project_id IS NULL) AS j, (SELECT count(*) FROM parse_errors WHERE project_id IS NULL) AS p, (SELECT count(*) FROM dns_records WHERE project_id IS NULL) AS r) n;

-- f4c8b2d0e6a1: every API key is an agent session's.
SELECT 'f4c8b2d0e6a1 API keys not bound to an agent session' AS revision, count(*) AS refused, '' AS detail FROM api_keys WHERE agent_id IS NULL OR agent_session_id IS NULL;

-- b6e0d4f2a8c3: one scanner row per identity (NULLs compare equal, as the unique index's NULLS NOT DISTINCT).
SELECT 'b6e0d4f2a8c3 scanner rows sharing one identity' AS revision, count(*) AS refused, 'extra rows: ' || coalesce(sum(n) - count(*), 0) AS detail FROM (SELECT count(*) AS n FROM vulnerabilities GROUP BY host_id, source, plugin_id, port_id, name_id, title HAVING count(*) > 1) d;
