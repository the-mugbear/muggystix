---
title: Internal Triage Report
audience: the testing team and whoever picks the work up next
description: >
  Working document rather than a deliverable: what is outstanding, who owns
  it, and what the next person needs in order to continue.
---

<!--
HOW TO FILL THIS IN

This one is for colleagues, so precision beats polish: host ids, exact
queries, and honest status. Keep the queries you used — the next person should
be able to re-run them rather than reconstruct your reasoning.
-->

# {{project_name}} — Internal Triage

**As of:** {{date}} · **Compiled by:** {{operator_name}}

## Outstanding work

<!-- assist_count_hosts for each number; keep the q= alongside it. -->

| Question | Query | Count |
|---|---|---|
| Critical findings with no owner | `has:critical AND assigned:none` | {{n}} |
| Hosts in review | `follow:in_review` | {{n}} |
| Assigned to me | `assigned:me` | {{n}} |
| Never reviewed | `follow:none` | {{n}} |
| {{your own question}} | `{{q}}` | {{n}} |

## Unowned critical findings

<!-- assist_list_hosts q="has:critical AND assigned:none", then
     assist_get_host_findings per host for the evidence. -->

| Host | Finding | Evidence | Suggested owner |
|---|---|---|---|
| {{ip}} (#{{host_id}}) | {{title}} | {{what it rests on}} | {{who, or "unassigned"}} |

## In flight

| Host | Owner | Status | Blocked on |
|---|---|---|---|
| {{ip}} (#{{host_id}}) | {{username}} | {{in review / testing / awaiting client}} | {{what would unblock it}} |

## Notes for whoever picks this up

<!-- assist_get_host on anything with a subtlety worth recording. -->

- {{the thing that is not obvious from the data — a host that looks
  vulnerable and is not, a service that is a false positive, an owner who
  asked to be consulted first}}

## Coverage gaps

- {{ranges not scanned, checks not run, credentials not available}}
- {{anything you would want to know if you were reading this cold}}
