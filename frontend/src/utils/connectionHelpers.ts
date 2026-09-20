import type { Port } from '../services/api';

export interface ConnectionHelper {
  /** Short label shown in the UI (e.g. "curl", "smbclient") */
  tool: string;
  /** Full command ready to paste into a terminal */
  command: string;
  /** Brief description of what the command does */
  description: string;
}

/**
 * Build a list of suggested terminal commands for a given port on a host.
 * Commands are pre-populated with the target IP/hostname so the user can
 * copy-paste them directly.
 */
export interface ConnectionHelperOptions {
  /** The named endpoint (virtual host) to address on a web port.  An address
   *  alone reaches the server's DEFAULT site: on shared hosting that is a
   *  different website, with a different certificate, from the one the
   *  evidence describes. */
  vhost?: string | null;
}

/** Scanner-reported names end up inside a command the operator pastes into a
 *  shell.  Anything that is not plainly a hostname is refused, not escaped. */
export const isSafeHostname = (name: string | null | undefined): name is string =>
  !!name && name.length <= 253 && /^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(name);

export const getConnectionHelpers = (
  ip: string,
  port: Port,
  // Unused today (helpers target the IP; `options.vhost` carries a name), but
  // positional — every caller passes it, so the slot stays.
  _hostname?: string | null,
  options: ConnectionHelperOptions = {},
): ConnectionHelper[] => {
  const target = ip;
  const pn = port.port_number;
  const svc = (port.service_name || '').toLowerCase();
  const helpers: ConnectionHelper[] = [];

  // --- Web / HTTP(S) ---
  if (isHttp(pn, svc)) {
    const scheme = isHttps(pn, svc) ? 'https' : 'http';
    const portSuffix = (scheme === 'https' && pn === 443) || (scheme === 'http' && pn === 80) ? '' : `:${pn}`;
    const vhost = isSafeHostname(options.vhost) ? options.vhost : null;
    // A bare IPv6 address needs brackets in a URL and in curl's --resolve.
    const bracketed = ip.includes(':') ? `[${ip}]` : ip;
    const base = `${scheme}://${vhost ?? bracketed}${portSuffix}`;

    if (vhost) {
      // --resolve pins the name to THIS address for one request: the right
      // Host header and SNI, with no dependence on the operator's resolver.
      helpers.push({
        tool: 'curl',
        command: `curl -ik --resolve ${vhost}:${pn}:${bracketed} ${base}/`,
        description: `Fetch ${vhost} from this address (Host + SNI set, DNS bypassed)`,
      });
      if (scheme === 'https') {
        helpers.push({
          tool: 'openssl',
          command: `openssl s_client -connect ${bracketed}:${pn} -servername ${vhost} </dev/null | openssl x509 -noout -subject -issuer -dates`,
          description: `The certificate served for ${vhost} (SNI), not the default site's`,
        });
      }
      helpers.push({
        tool: 'hosts entry',
        command: `echo '${ip} ${vhost}' | sudo tee -a /etc/hosts`,
        description: `The tools below use the name and cannot pin an address: make ${vhost} resolve here first if it does not`,
      });
    } else {
      helpers.push({
        tool: 'curl',
        command: `curl -ik ${base}/`,
        description: 'Fetch headers and body (ignore cert errors)',
      });
      if (scheme === 'https') {
        helpers.push({
          tool: 'openssl',
          command: `openssl s_client -connect ${bracketed}:${pn} </dev/null | openssl x509 -noout -subject -issuer -dates`,
          description: 'The certificate of the default site at this address (no SNI)',
        });
      }
    }
    helpers.push({
      tool: 'whatweb',
      command: `whatweb --color=never ${base}`,
      description: 'Identify web technologies',
    });
    helpers.push({
      tool: 'gobuster',
      command: `gobuster dir -u ${base} -w /usr/share/wordlists/dirb/common.txt -k -t 50`,
      description: 'Directory brute-force',
    });
    helpers.push({
      tool: 'feroxbuster',
      command: `feroxbuster -u ${base} -k -t 50`,
      description: 'Recursive content discovery',
    });
    helpers.push({
      tool: 'ffuf',
      command: `ffuf -u ${base}/FUZZ -w /usr/share/wordlists/dirb/common.txt -mc all -fc 404`,
      description: 'Fuzz paths and parameters',
    });
    helpers.push({
      tool: 'dirsearch',
      command: `dirsearch -u ${base} -t 50`,
      description: 'Web path scanner',
    });
    helpers.push({
      tool: 'nikto',
      command: `nikto -h ${base}`,
      description: 'Web vulnerability scanner',
    });
  }

  // --- SSH ---
  if (pn === 22 || svc === 'ssh') {
    helpers.push({
      tool: 'ssh',
      command: `ssh ${target} -p ${pn}`,
      description: 'Connect via SSH',
    });
    helpers.push({
      tool: 'ssh-audit',
      command: `ssh-audit ${target}:${pn}`,
      description: 'Audit SSH configuration',
    });
  }

  // --- SMB ---
  if (pn === 445 || pn === 139 || svc === 'microsoft-ds' || svc === 'netbios-ssn' || svc === 'smb') {
    helpers.push({
      tool: 'smbclient',
      command: `smbclient -L //${target} -N`,
      description: 'List SMB shares (null session)',
    });
    helpers.push({
      tool: 'smbmap',
      command: `smbmap -H ${target}`,
      description: 'Enumerate share permissions',
    });
    helpers.push({
      tool: 'netexec',
      command: `netexec smb ${target}`,
      description: 'SMB enumeration with NetExec',
    });
    helpers.push({
      tool: 'enum4linux',
      command: `enum4linux -a ${target}`,
      description: 'Full SMB/NetBIOS enumeration',
    });
  }

  // --- RDP ---
  if (pn === 3389 || svc === 'ms-wbt-server' || svc === 'rdp') {
    helpers.push({
      tool: 'xfreerdp',
      command: `xfreerdp /v:${target}:${pn} /cert:ignore`,
      description: 'Connect via RDP',
    });
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script rdp-enum-encryption,rdp-ntlm-info ${target}`,
      description: 'Enumerate RDP security settings',
    });
  }

  // --- FTP ---
  if (pn === 21 || svc === 'ftp') {
    helpers.push({
      tool: 'ftp',
      command: `ftp ${target} ${pn}`,
      description: 'Connect via FTP',
    });
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script ftp-anon,ftp-syst ${target}`,
      description: 'Check anonymous FTP and system info',
    });
  }

  // --- Telnet ---
  if (pn === 23 || svc === 'telnet') {
    helpers.push({
      tool: 'telnet',
      command: `telnet ${target} ${pn}`,
      description: 'Connect via Telnet',
    });
  }

  // --- SMTP ---
  if (pn === 25 || pn === 587 || pn === 465 || svc === 'smtp') {
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script smtp-commands,smtp-enum-users ${target}`,
      description: 'Enumerate SMTP commands and users',
    });
    helpers.push({
      tool: 'nc',
      command: `nc -nv ${target} ${pn}`,
      description: 'Banner grab SMTP service',
    });
  }

  // --- DNS ---
  if (pn === 53 || svc === 'domain' || svc === 'dns') {
    helpers.push({
      tool: 'dig',
      command: `dig @${target} version.bind txt chaos`,
      description: 'Query DNS version',
    });
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script dns-zone-transfer --script-args dns-zone-transfer.domain=DOMAIN ${target}`,
      description: 'Attempt DNS zone transfer',
    });
  }

  // --- SNMP ---
  if (pn === 161 || svc === 'snmp') {
    helpers.push({
      tool: 'snmpwalk',
      command: `snmpwalk -v2c -c public ${target}`,
      description: 'Walk SNMP tree with community string',
    });
    helpers.push({
      tool: 'onesixtyone',
      command: `onesixtyone ${target} -c /usr/share/seclists/Discovery/SNMP/common-snmp-community-strings.txt`,
      description: 'Brute-force SNMP community strings',
    });
  }

  // --- LDAP ---
  if (pn === 389 || pn === 636 || svc === 'ldap' || svc === 'ldaps') {
    helpers.push({
      tool: 'ldapsearch',
      command: `ldapsearch -x -H ldap://${target}:${pn} -b "" -s base namingContexts`,
      description: 'Query LDAP root DSE',
    });
    helpers.push({
      tool: 'netexec',
      command: `netexec ldap ${target}`,
      description: 'LDAP enumeration with NetExec',
    });
  }

  // --- WinRM ---
  if (pn === 5985 || pn === 5986 || svc === 'wsman') {
    helpers.push({
      tool: 'netexec',
      command: `netexec winrm ${target}`,
      description: 'Test WinRM connectivity',
    });
  }

  // --- MySQL ---
  if (pn === 3306 || svc === 'mysql') {
    helpers.push({
      tool: 'mysql',
      command: `mysql -h ${target} -P ${pn} -u root -p`,
      description: 'Connect to MySQL',
    });
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script mysql-info,mysql-enum ${target}`,
      description: 'Enumerate MySQL',
    });
  }

  // --- MSSQL ---
  if (pn === 1433 || svc === 'ms-sql-s' || svc === 'mssql') {
    helpers.push({
      tool: 'impacket',
      command: `impacket-mssqlclient ${target} -port ${pn} -windows-auth`,
      description: 'Connect to MSSQL',
    });
    helpers.push({
      tool: 'netexec',
      command: `netexec mssql ${target}`,
      description: 'MSSQL enumeration with NetExec',
    });
  }

  // --- PostgreSQL ---
  if (pn === 5432 || svc === 'postgresql') {
    helpers.push({
      tool: 'psql',
      command: `psql -h ${target} -p ${pn} -U postgres`,
      description: 'Connect to PostgreSQL',
    });
  }

  // --- Redis ---
  if (pn === 6379 || svc === 'redis') {
    helpers.push({
      tool: 'redis-cli',
      command: `redis-cli -h ${target} -p ${pn} INFO`,
      description: 'Connect and get Redis info',
    });
  }

  // --- VNC ---
  if (pn === 5900 || pn === 5901 || svc === 'vnc') {
    helpers.push({
      tool: 'vncviewer',
      command: `vncviewer ${target}:${pn}`,
      description: 'Connect via VNC',
    });
  }

  // --- Kerberos ---
  if (pn === 88 || svc === 'kerberos') {
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} --script krb5-enum-users --script-args krb5-enum-users.realm=DOMAIN ${target}`,
      description: 'Enumerate Kerberos users',
    });
  }

  // --- Generic fallback: nc banner grab ---
  if (helpers.length === 0) {
    helpers.push({
      tool: 'nc',
      command: `nc -nv ${target} ${pn}`,
      description: 'Banner grab / raw connect',
    });
    helpers.push({
      tool: 'nmap',
      command: `nmap -p ${pn} -sV -sC ${target}`,
      description: 'Service version and default scripts',
    });
  }

  return helpers;
};

// --- Internal helpers ---

const HTTPS_PORTS = new Set([443, 8443, 9443, 4443]);
const HTTP_PORTS = new Set([80, 8080, 8081, 8000, 8008, 8888, 9090, 8181, 3000]);

function isHttp(port: number, svc: string): boolean {
  if (HTTP_PORTS.has(port) || HTTPS_PORTS.has(port)) return true;
  return svc.includes('http') || svc.includes('web') || svc.includes('ssl');
}

function isHttps(port: number, svc: string): boolean {
  if (HTTPS_PORTS.has(port)) return true;
  return svc.includes('https') || svc.includes('ssl');
}
