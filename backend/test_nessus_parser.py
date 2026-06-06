#!/usr/bin/env python3
"""
Test script for Nessus parser functionality
"""

import sys
import os
import tempfile
from datetime import datetime

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.parsers.nessus_parser import NessusParser


def create_sample_nessus_xml():
    """Create a sample Nessus XML file for testing"""
    sample_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<NessusClientData_v2>
    <Policy>
        <policyName>Sample Security Scan</policyName>
    </Policy>
    <Report name="Sample Nessus Report">
        <ReportHost name="192.168.1.100">
            <HostProperties>
                <tag name="host-ip">192.168.1.100</tag>
                <tag name="host-fqdn">server.example.com</tag>
                <tag name="operating-system">Microsoft Windows Server 2019 Standard</tag>
                <tag name="netbios-name">SERVER01</tag>
                <tag name="mac-address">00:0C:29:12:34:56</tag>
            </HostProperties>

            <!-- Critical vulnerability - EternalBlue -->
            <ReportItem port="445" svc_name="smb" protocol="tcp" severity="4" pluginID="97833" pluginName="MS17-010: Security Update for Microsoft Windows SMB Server (4013389) (ETERNALBLUE) (ETERNALCHAMPION) (ETERNALROMANCE) (ETERNALSYNERGY) (WannaCry) (EternalRocks) (Petya)">
                <risk_factor>Critical</risk_factor>
                <cvss_base_score>9.3</cvss_base_score>
                <cvss_vector>CVSS2#AV:N/AC:M/Au:N/C:C/I:C/A:C</cvss_vector>
                <cvss3_base_score>8.1</cvss3_base_score>
                <cvss3_vector>CVSS:3.0/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H</cvss3_vector>
                <cve>CVE-2017-0144, CVE-2017-0145, CVE-2017-0146, CVE-2017-0147, CVE-2017-0148</cve>
                <description>The remote Windows host is affected by multiple vulnerabilities in Microsoft Windows SMB Server.</description>
                <synopsis>The remote Windows host is affected by multiple remote code execution vulnerabilities.</synopsis>
                <solution>Microsoft has released a set of patches for Windows Vista, 2008, 7, 2008 R2, 2012, 8.1, 2012 R2, 10, and 2016.</solution>
                <exploit_available>true</exploit_available>
                <metasploit_name>exploit/windows/smb/ms17_010_eternalblue</metasploit_name>
                <patch_publication_date>2017/03/14</patch_publication_date>
                <vuln_publication_date>2017/03/14</vuln_publication_date>
                <plugin_output>Remote Windows SMB server is vulnerable to EternalBlue attack</plugin_output>
            </ReportItem>

            <!-- High vulnerability - BlueKeep -->
            <ReportItem port="3389" svc_name="rdp" protocol="tcp" severity="3" pluginID="125022" pluginName="MS19-0181: Security Update for Remote Desktop Services (4500331) (BlueKeep)">
                <risk_factor>High</risk_factor>
                <cvss_base_score>7.5</cvss_base_score>
                <cvss3_base_score>9.8</cvss3_base_score>
                <cvss3_vector>CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H</cvss3_vector>
                <cve>CVE-2019-0708</cve>
                <description>The remote Windows host is missing a security update for Remote Desktop Services.</description>
                <synopsis>The remote Windows host is affected by a remote code execution vulnerability.</synopsis>
                <solution>Apply the relevant security update referenced in Microsoft Security Advisory 4500331.</solution>
                <exploit_available>false</exploit_available>
                <patch_publication_date>2019/05/14</patch_publication_date>
                <vuln_publication_date>2019/05/14</vuln_publication_date>
            </ReportItem>

            <!-- Medium vulnerability - Weak SSL -->
            <ReportItem port="443" svc_name="https" protocol="tcp" severity="2" pluginID="57582" pluginName="SSL Weak Cipher Suites Supported">
                <risk_factor>Medium</risk_factor>
                <cvss_base_score>4.3</cvss_base_score>
                <cvss_vector>CVSS2#AV:N/AC:M/Au:N/C:P/I:N/A:N</cvss_vector>
                <description>The remote host supports the use of SSL ciphers that offer weak encryption.</description>
                <synopsis>The remote service supports the use of weak SSL ciphers.</synopsis>
                <solution>Reconfigure the affected application if possible to avoid use of weak ciphers.</solution>
                <plugin_output>List of weak cipher suites supported by the remote server: TLSv1.0 DES-CBC3-SHA</plugin_output>
            </ReportItem>

            <!-- Low vulnerability - HTTP Methods -->
            <ReportItem port="80" svc_name="http" protocol="tcp" severity="1" pluginID="24260" pluginName="HyperText Transfer Protocol (HTTP) Methods Allowed (per directory) : OPTIONS TRACE">
                <risk_factor>Low</risk_factor>
                <cvss_base_score>2.6</cvss_base_score>
                <description>Calling the OPTIONS method gives an attacker information about which HTTP methods are supported.</description>
                <synopsis>The remote web server supports the OPTIONS and/or TRACE methods.</synopsis>
                <solution>Disable these methods.</solution>
            </ReportItem>

            <!-- Info finding - OS Detection -->
            <ReportItem port="0" protocol="tcp" severity="0" pluginID="11936" pluginName="OS Identification">
                <risk_factor>None</risk_factor>
                <description>It is possible to guess the remote operating system type and version by connecting to the remote host and examining some of its characteristics.</description>
                <synopsis>The remote operating system can be identified.</synopsis>
                <solution>N/A</solution>
                <plugin_output>Remote operating system : Microsoft Windows Server 2019 Standard</plugin_output>
            </ReportItem>
        </ReportHost>

        <!-- Second host with fewer vulnerabilities -->
        <ReportHost name="192.168.1.101">
            <HostProperties>
                <tag name="host-ip">192.168.1.101</tag>
                <tag name="host-fqdn">web.example.com</tag>
                <tag name="operating-system">Ubuntu Linux 20.04</tag>
                <tag name="netbios-name">WEB01</tag>
            </HostProperties>

            <!-- Medium vulnerability - Apache -->
            <ReportItem port="80" svc_name="http" protocol="tcp" severity="2" pluginID="40984" pluginName="Apache HTTP Server Information Disclosure">
                <risk_factor>Medium</risk_factor>
                <cvss_base_score>5.0</cvss_base_score>
                <description>The remote web server leaks information via HTTP headers.</description>
                <synopsis>The remote web server reveals unnecessary information.</synopsis>
                <solution>Modify the HTTP server's configuration to limit the information disclosed in HTTP headers.</solution>
                <plugin_output>Server: Apache/2.4.41 (Ubuntu)</plugin_output>
            </ReportItem>
        </ReportHost>
    </Report>
</NessusClientData_v2>'''

    return sample_xml


def test_nessus_parser():
    """Test the Nessus parser with sample data"""
    print("Testing Nessus Parser...")
    print("=" * 50)

    # Create sample Nessus XML
    sample_xml = create_sample_nessus_xml()

    # Create temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.nessus', delete=False) as temp_file:
        temp_file.write(sample_xml)
        temp_file_path = temp_file.name

    try:
        # Initialize parser
        parser = NessusParser()

        # Test file validation
        print("1. Testing file validation...")
        is_valid, message = parser.validate_file(temp_file_path)
        print(f"   Valid: {is_valid}")
        print(f"   Message: {message}")
        print()

        if not is_valid:
            print("❌ File validation failed!")
            return False

        # Test parsing
        print("2. Testing file parsing...")
        result = parser.parse_file(temp_file_path)

        # Display results
        print(f"   Parser type: {result['parser_type']}")
        print(f"   Parser version: {result['parser_version']}")
        print()

        # Scan info
        scan_info = result['scan_info']
        print("3. Scan Information:")
        print(f"   Report name: {scan_info.get('report_name')}")
        print(f"   Policy name: {scan_info.get('policy_name')}")
        print()

        # Statistics
        stats = result['statistics']
        print("4. Scan Statistics:")
        print(f"   Total hosts: {stats['total_hosts']}")
        print(f"   Total vulnerabilities: {stats['total_vulnerabilities']}")
        print(f"   Critical: {stats['vulnerability_counts']['Critical']}")
        print(f"   High: {stats['vulnerability_counts']['High']}")
        print(f"   Medium: {stats['vulnerability_counts']['Medium']}")
        print(f"   Low: {stats['vulnerability_counts']['Low']}")
        print(f"   Info: {stats['vulnerability_counts']['Info']}")
        print(f"   Exploitable vulnerabilities: {stats['exploitable_vulnerabilities']}")
        print(f"   Unique CVEs: {stats['unique_cves']}")
        print()

        # Host details
        hosts = result['hosts']
        print("5. Host Details:")
        for i, host in enumerate(hosts, 1):
            print(f"   Host {i}: {host.ip_address}")
            print(f"     Hostname: {host.hostname or 'N/A'}")
            print(f"     OS: {host.operating_system or 'Unknown'}")
            print(f"     MAC: {host.mac_address or 'N/A'}")
            print(f"     Vulnerabilities: {len(host.vulnerabilities)}")

            # Show top vulnerabilities
            critical_vulns = [v for v in host.vulnerabilities if v.severity == 4]
            high_vulns = [v for v in host.vulnerabilities if v.severity == 3]

            if critical_vulns:
                print(f"     Critical vulnerabilities:")
                for vuln in critical_vulns[:3]:  # Show top 3
                    print(f"       - {vuln.plugin_name}")
                    if vuln.cve_list:
                        print(f"         CVE: {', '.join(vuln.cve_list[:3])}")
                    print(f"         CVSS: {vuln.cvss3_base_score or vuln.cvss_base_score or 'N/A'}")
                    print(f"         Exploitable: {vuln.exploitable}")

            if high_vulns:
                print(f"     High vulnerabilities:")
                for vuln in high_vulns[:2]:  # Show top 2
                    print(f"       - {vuln.plugin_name}")
                    if vuln.cve_list:
                        print(f"         CVE: {', '.join(vuln.cve_list[:3])}")
            print()

        # OS distribution
        if stats['os_distribution']:
            print("6. Operating System Distribution:")
            for os_name, count in stats['os_distribution'].items():
                print(f"   {os_name}: {count} hosts")
            print()

        # CVE list sample
        if stats['cve_list']:
            print("7. Sample CVEs found:")
            for cve in stats['cve_list'][:10]:  # Show first 10
                print(f"   - {cve}")
            if len(stats['cve_list']) > 10:
                print(f"   ... and {len(stats['cve_list']) - 10} more")
            print()

        print("✅ Nessus parser test completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Clean up temporary file
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


def test_content_parsing():
    """Test parsing from content string"""
    print("\nTesting content parsing...")
    print("=" * 30)

    sample_xml = create_sample_nessus_xml()
    parser = NessusParser()

    try:
        result = parser.parse_content(sample_xml)
        print(f"✅ Content parsing successful")
        print(f"   Hosts parsed: {len(result['hosts'])}")
        print(f"   Total vulnerabilities: {result['statistics']['total_vulnerabilities']}")
        return True
    except Exception as e:
        print(f"❌ Content parsing failed: {e}")
        return False


if __name__ == "__main__":
    print("Nessus Parser Test Suite")
    print("=" * 60)
    print(f"Test started at: {datetime.now()}")
    print()

    # Run tests
    file_test_passed = test_nessus_parser()
    content_test_passed = test_content_parsing()

    print("\n" + "=" * 60)
    print("Test Summary:")
    print(f"  File parsing test: {'✅ PASSED' if file_test_passed else '❌ FAILED'}")
    print(f"  Content parsing test: {'✅ PASSED' if content_test_passed else '❌ FAILED'}")

    if file_test_passed and content_test_passed:
        print("\n🎉 All tests passed! Nessus parser is working correctly.")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed. Please check the output above.")
        sys.exit(1)