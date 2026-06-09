# CVE Database Integration Plan

> **Status:** Unimplemented planning document. No live CVE API integration exists — CVE data currently comes from Nessus/OpenVAS scan imports only. Still accurate as of backend 2.115.0 (2026-06-07): no live CVE feed has been wired in.
> **Written against:** backend 1.3.3 (2026-03-31)

## Real CVE Integration Options

### 1. National Vulnerability Database (NVD) API
**URL:** https://nvd.nist.gov/developers/vulnerabilities
**API Key:** Required (free)
**Rate Limits:** 50 requests per 30 seconds (without key), 5000/30s (with key)
**Data Format:** JSON API with CVE details, CVSS scores, CWE mappings

```python
# Example API call
GET https://services.nvd.nist.gov/rest/json/cves/2.0
?cveId=CVE-2021-44228
&apiKey=YOUR_API_KEY
```

### 2. MITRE CVE API
**URL:** https://cveawg.mitre.org/api/
**API Key:** None required
**Rate Limits:** More permissive
**Data Format:** JSON with basic CVE information


### 3. CVE JSON Data Feeds
**URL:** https://github.com/CVEProject/cvelistV5
**Format:** Git repository with JSON files
**Update Method:** Git pulls or archive downloads

### 4. Commercial Solutions
- **VulnDB** (Rapid7)
- **Vulners API**
- **CVEDetails API**