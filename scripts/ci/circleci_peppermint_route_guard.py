#!/usr/bin/env python3
"""Fail closed if a temporary tailnet changes CircleCI VM control routes."""

import ipaddress
import json
import re
import sys
from collections import Counter
from pathlib import Path

RESERVED = ipaddress.ip_network("192.168.53.0/24")
FIXED_SENTINELS = ("1.1.1.1", "169.254.169.254", "192.168.53.1")


def routes(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Invalid IPv4 route snapshot")
    return value


def rules(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Invalid IPv4 rule snapshot")
    return value


def network(row):
    destination = row.get("dst", "default")
    if destination == "default":
        return ipaddress.ip_network("0.0.0.0/0")
    return ipaddress.ip_network(destination, strict=False)


def route_signature(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def verify(before_routes, after_routes, before_rules, after_rules):
    before = routes(before_routes)
    after = routes(after_routes)
    original = Counter(route_signature(row) for row in before)
    current = Counter(route_signature(row) for row in after)
    if original - current:
        raise ValueError("Native tailnet removed or changed a pre-existing VM route")
    original_rules = Counter(json.dumps(row, sort_keys=True) for row in rules(before_rules))
    current_rules = Counter(json.dumps(row, sort_keys=True) for row in rules(after_rules))
    if original_rules - current_rules:
        raise ValueError("Native tailnet removed or changed a pre-existing VM routing rule")
    existing = [network(row) for row in before if network(row).prefixlen]
    tailnet = [network(row) for row in after if row.get("dev") == "tailscale0"]
    if not tailnet:
        raise ValueError("Native tailnet installed no routes")
    for route in tailnet:
        if route.prefixlen == 0 or route.overlaps(RESERVED):
            raise ValueError(f"Native tailnet overlaps CircleCI control network: {route}")
        if any(route.overlaps(prior) for prior in existing):
            raise ValueError(f"Native tailnet overlaps a pre-existing VM route: {route}")


def sentinels(route_path, resolv_path):
    found = set(FIXED_SENTINELS)
    for row in routes(route_path):
        gateway = row.get("gateway")
        if gateway:
            try:
                found.add(str(ipaddress.IPv4Address(gateway)))
            except ipaddress.AddressValueError:
                pass
    for address in re.findall(r"^\s*nameserver\s+(\S+)", Path(resolv_path).read_text(), flags=re.MULTILINE):
        try:
            found.add(str(ipaddress.IPv4Address(address)))
        except ipaddress.AddressValueError:
            pass
    return sorted(found, key=ipaddress.IPv4Address)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "sentinels":
        print("\n".join(sentinels(sys.argv[2], sys.argv[3])))
    elif len(sys.argv) == 6 and sys.argv[1] == "verify":
        verify(*sys.argv[2:])
    else:
        raise SystemExit("Usage: route_guard.py sentinels ROUTES RESOLV | verify BEFORE_ROUTES AFTER_ROUTES BEFORE_RULES AFTER_RULES")


if __name__ == "__main__":
    main()
