---
title: Client Engagement Summary
audience: client stakeholders (non-technical readers included)
description: >
  End-of-engagement summary for the customer: what was assessed, what was
  found, what to do about it, and how much confidence to place in the answer.
---

<!--
HOW TO FILL THIS IN

Replace every {{placeholder}} and delete every one of these HTML comments
before you hand the document over — a comment left in is a comment the client
reads.

Each section names the tool that supplies its data. Where a number is asked
for, get it from `assist_count_hosts`, not by counting a page of
`assist_list_hosts` — a page is not a total, and a count that stopped at the
first page is wrong in a way the reader cannot see.

Do not assert a finding this project's data does not contain. If something is
unknown, the honest sentence is "not assessed", and §5 exists to say so.
-->

# {{project_name}} — Security Assessment Summary

**Prepared for:** {{client_name}}
**Assessment window:** {{start_date}} – {{end_date}}
**Prepared by:** {{operator_name}}

---

## 1. What was assessed

<!-- assist_get_context for the totals; assist_list_scopes for the ranges. -->

{{one paragraph: the ranges in scope, how many hosts were discovered, and the
period the data covers. State the scope in CIDRs — a client can verify a CIDR,
they cannot verify "the internal network".}}

| | |
|---|---|
| Hosts discovered | {{host_count}} |
| Network ranges in scope | {{scope_list}} |
| Scans ingested | {{scan_count}} |
| Assessment period | {{start_date}} – {{end_date}} |

## 2. Headline result

<!-- assist_count_hosts with q=has:critical, then q=has:high, etc. -->

{{two or three sentences a non-technical reader can act on. Lead with the thing
that would matter to them if they read nothing else. Avoid tool names and CVE
numbers here — those belong in §3.}}

| Severity | Hosts affected |
|---|---|
| Critical | {{critical_host_count}} |
| High | {{high_host_count}} |
| Medium | {{medium_host_count}} |

## 3. Findings that need action

<!--
For each: assist_list_hosts with a q= that isolates it, then
assist_get_host_findings on the affected hosts for the evidence.
Order by what you would fix first, not by severity label alone — an
internet-reachable medium can outrank an isolated critical, and saying so is
the value you add over a scanner.
-->

### 3.1 {{finding_title}}

- **Affected:** {{n}} hosts — {{representative ip addresses}}
- **What it is:** {{plain-language explanation, two sentences}}
- **Why it matters here:** {{the consequence in this environment, not the
  generic CVSS narrative}}
- **Evidence:** {{the specific observation — service, version, banner, or
  finding id — that this rests on}}
- **Recommended action:** {{concrete, ordered, and achievable}}

{{repeat 3.x per finding worth a client's attention}}

## 4. Recommended sequence

<!-- Your judgement, informed by the data. A list of everything is not a plan. -->

1. {{the thing to do first, and why it is first}}
2. {{second}}
3. {{third}}

## 5. Scope and confidence

<!--
The section that makes the rest trustworthy. An assessment that does not say
what it could not see invites the reader to assume it saw everything.
-->

- **Assessed:** {{what the data actually covers}}
- **Not assessed:** {{ranges, hosts, or classes of issue outside this work}}
- **Point-in-time:** these results reflect the environment as of {{end_date}};
  changes after that date are not represented.
- **Known gaps:** {{hosts that did not respond, credentialed checks not run,
  segments unreachable from the testing position — say so plainly}}
