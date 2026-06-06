#!/usr/bin/env python3
"""
Simple test script for NetExec parser functionality.
"""

import tempfile
import os
import sys

# Add the backend directory to the path
sys.path.insert(0, '/home/charles/Documents/NetworkMapper/backend')

from app.parsers.netexec_parser import NetexecParser
from app.services.confidence_service import ConfidenceService, ScanType, DataSource
from app.db.session import SessionLocal

# Sample NetExec output for testing
SAMPLE_NETEXEC_OUTPUT = """
SMB         192.168.1.100   445    WORKSTATION1     [*] Windows 10.0 Build 19041 x64 (name:WORKSTATION1) (domain:TESTDOMAIN) (signing:False) (SMBv1:False)
SMB         192.168.1.101   445    DC01             [*] Windows Server 2019 Build 17763 x64 (name:DC01) (domain:TESTDOMAIN) (signing:True) (SMBv1:False)
LDAP        192.168.1.101   389    DC01             [*] Windows Server 2019 Build 17763 x64 (domain: TESTDOMAIN)
SMB         192.168.1.102   445    FILESERVER       [+] TESTDOMAIN\testuser:password123
WINRM       192.168.1.100   5985   WORKSTATION1     [-] TESTDOMAIN\testuser:password123
RDP         192.168.1.103   3389   RDPSERVER        [*] Windows Server 2016 Standard 14393 x64
"""

SAMPLE_NETEXEC_JSON = """
{
  "192.168.1.100": {
    "ADMIN$": {
      "desktop.ini": {
        "atime_epoch": "2024-01-15 10:30:45",
        "ctime_epoch": "2024-01-15 10:30:45",
        "mtime_epoch": "2024-01-15 10:30:45",
        "size": "174 B"
      }
    },
    "C$": {
      "Users/Administrator/Documents/test.txt": {
        "atime_epoch": "2024-01-15 11:45:20",
        "ctime_epoch": "2024-01-15 11:45:20",
        "mtime_epoch": "2024-01-15 11:45:20",
        "size": "1.2 KB"
      }
    }
  }
}
"""

def test_confidence_service():
    """Test the confidence service calculations"""
    print("Testing Confidence Service...")

    confidence_service = ConfidenceService()

    # Test nmap confidence
    nmap_conf = confidence_service.calculate_confidence(
        scan_type=ScanType.NMAP,
        data_source=DataSource.SERVICE_BANNER,
        method="nmap -sV",
        additional_factors={"service_conf": 9}
    )
    print(f"Nmap -sV service banner confidence: {nmap_conf}")

    # Test netexec confidence
    netexec_conf = confidence_service.calculate_confidence(
        scan_type=ScanType.NETEXEC,
        data_source=DataSource.SMB_ENUM,
        method="netexec smb",
        additional_factors={"enumeration_success": True}
    )
    print(f"NetExec SMB enumeration confidence: {netexec_conf}")

    # Test masscan confidence
    masscan_conf = confidence_service.calculate_confidence(
        scan_type=ScanType.MASSCAN,
        data_source=DataSource.CONNECTION_TEST,
        method="masscan",
        additional_factors={}
    )
    print(f"Masscan connection test confidence: {masscan_conf}")

    print("✓ Confidence service tests completed\n")

def test_netexec_parser():
    """Test the NetExec parser with sample data"""
    print("Testing NetExec Parser...")

    # Create a database session
    db = SessionLocal()

    try:
        parser = NetexecParser(db)

        # Test console output parsing
        print("Testing console output parsing...")
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(SAMPLE_NETEXEC_OUTPUT)
            console_file = f.name

        try:
            scan1 = parser.parse_file(console_file, "netexec_console_output.txt")
            print(f"✓ Console output parsed successfully. Scan ID: {scan1.id}")
            db.commit()
        finally:
            os.unlink(console_file)

        # Test JSON output parsing
        print("Testing JSON output parsing...")
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(SAMPLE_NETEXEC_JSON)
            json_file = f.name

        try:
            scan2 = parser.parse_file(json_file, "netexec_spider_output.json")
            print(f"✓ JSON output parsed successfully. Scan ID: {scan2.id}")
            db.commit()
        finally:
            os.unlink(json_file)

        print("✓ NetExec parser tests completed\n")

    except Exception as e:
        print(f"✗ NetExec parser test failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def test_content_detection():
    """Test content detection functions"""
    print("Testing content detection...")

    # Import the functions from upload.py
    sys.path.insert(0, '/home/charles/Documents/NetworkMapper/backend/app/api/v1/endpoints')
    from upload import _is_netexec_content, _is_netexec_json_content

    # Test console output detection
    console_bytes = SAMPLE_NETEXEC_OUTPUT.encode('utf-8')
    is_netexec_console = _is_netexec_content(console_bytes)
    print(f"Console output detected as NetExec: {is_netexec_console}")

    # Test JSON output detection
    json_bytes = SAMPLE_NETEXEC_JSON.encode('utf-8')
    is_netexec_json = _is_netexec_json_content(json_bytes)
    print(f"JSON output detected as NetExec: {is_netexec_json}")

    # Test false positive (should not be detected as NetExec)
    nmap_output = """
    Nmap scan report for 192.168.1.1
    Host is up (0.00015s latency).
    PORT   STATE SERVICE
    22/tcp open  ssh
    80/tcp open  http
    """.encode('utf-8')

    is_nmap_detected_as_netexec = _is_netexec_content(nmap_output)
    print(f"Nmap output incorrectly detected as NetExec: {is_nmap_detected_as_netexec}")

    print("✓ Content detection tests completed\n")

def main():
    """Run all tests"""
    print("=== NetExec Parser Integration Tests ===\n")

    try:
        test_confidence_service()
        test_content_detection()
        test_netexec_parser()

        print("=== All Tests Completed Successfully ===")

    except Exception as e:
        print(f"=== Test Failed: {e} ===")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())