#!/usr/bin/env python3
"""Bind one verified private Blob endpoint on an ephemeral CI runner."""
import argparse
import ipaddress
from pathlib import Path
import re
import socket
import ssl


def verify_tls(host, address):
    # Connect privately while preserving hostname and normal certificate checks.
    with socket.create_connection((address, 443), timeout=15) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
            pass


def bind(account, address, hosts=Path('/etc/hosts'), verify_only=False):
    if not re.fullmatch(r'[a-z0-9]{3,24}', account):
        raise ValueError('Invalid storage account')
    ip = ipaddress.ip_address(address)
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if ip.version != 4 or not any(ip in ipaddress.ip_network(n) for n in networks):
        raise ValueError('Endpoint must be an explicit RFC1918 IPv4 address')
    host = account + '.blob.core.windows.net'
    content = hosts.read_text()
    existing = set()
    for line in content.splitlines():
        fields = line.split('#', 1)[0].split()
        if len(fields) > 1 and host in [value.lower() for value in fields[1:]]:
            existing.add(fields[0])
    if existing and existing != {address}:
        raise ValueError('Conflicting endpoint binding requires explicit review')
    verify_tls(host, address)
    if not verify_only and not existing:
        with hosts.open('a') as output:
            output.write('\n' + address + ' ' + host + '\n')
    print('Private journal endpoint verified' + (' (no changes)' if verify_only else ' and bound'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--address', required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    try:
        bind(args.account, args.address, verify_only=args.verify_only)
    except (ValueError, OSError) as exc:
        raise SystemExit('Private endpoint validation failed: ' + type(exc).__name__) from None
