import { describe, it, expect } from 'vitest';

import { isIpOrCidr, isIPv4Address, isIPv6Address } from '../../utils/ipAddress';

describe('isIpOrCidr — what ipaddress.ip_network(strict=False) accepts', () => {
  it.each([
    '10.0.0.5',
    '10.0.0.0/24',
    '10.0.0.5/24', // host bits set: strict=False normalises it
    '0.0.0.0/0',
    '255.255.255.255/32',
    ' 192.168.1.0/24 ',
    '10.0.0.0/255.255.255.0', // netmask
    '10.0.0.0/0.0.0.255', // hostmask
    '::1',
    '::',
    '2001:db8::/32',
    '2001:db8:0:0:0:0:0:1',
    'fe80::1/64',
    '::ffff:10.0.0.1',
    '1:2:3:4:5:6:7::',
  ])('accepts %s', (v) => {
    expect(isIpOrCidr(v)).toBe(true);
  });

  it.each([
    '',
    '   ',
    '10.0.0.300/24',
    '10.0.0.300',
    '10.0.0/24',
    '10.0.0.0/33',
    '10.0.0.0/-1',
    '10.0.0.0/',
    '10.0.0.0/24/8',
    '010.0.0.1', // leading zeros are rejected by Python too
    '10.0.0.0/255.0.255.0', // not a contiguous mask
    'example.com',
    '2001:db8::/129',
    '2001:db8:::1',
    '1::2::3',
    '1:2:3:4:5:6:7:8:9',
    '1:2:3:4:5:6:7',
    'gggg::1',
    '::1.2.3.4:5',
  ])('rejects %s', (v) => {
    expect(isIpOrCidr(v)).toBe(false);
  });

  it('keeps the address checks separate', () => {
    expect(isIPv4Address('10.0.0.1')).toBe(true);
    expect(isIPv4Address('::1')).toBe(false);
    expect(isIPv6Address('::1')).toBe(true);
    expect(isIPv6Address('10.0.0.1')).toBe(false);
  });
});
