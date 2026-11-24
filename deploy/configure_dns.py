"""Legt demo.home.arpa in der UniFi-DNS an (A-Eintrag auf den Heimserver).

Standardmäßig nur lesend. --apply braucht den SHA-256 des frisch gelesenen
DNS-Zustands (--expected-sha256), damit nichts Zwischenzeitliches überschrieben
wird. Fasst ausschließlich diesen einen Eintrag an.

Nutzt den gemeinsamen Helfer aus D:/Projects/network-misc.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, 'D:/Projects/network-misc/hosts/heimserver/lan-access')
from configure_service_dns import CONTROLLER, call, key, state_sha256, verify_step  # noqa: E402

RECORD = {'key': 'demo.home.arpa', 'record_type': 'A',
          'value': '192.168.1.175', 'enabled': True, 'ttl': 300}


def plan(records):
    matches = [row for row in records if key(row) == RECORD['key']]
    if len(matches) > 1:
        raise RuntimeError('Doppelter DNS-Eintrag für demo.home.arpa')
    if matches:
        if any(matches[0].get(field) != value for field, value in RECORD.items()):
            raise RuntimeError('Vorhandener demo.home.arpa-Eintrag weicht ab; wird nicht überschrieben')
        return []
    return [{'method': 'POST', 'key': RECORD['key'], 'body': RECORD.copy()}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    before = call(CONTROLLER)
    before_hash = state_sha256(before)
    operations = plan(before)
    after = before
    if args.apply and operations:
        if args.expected_sha256 != before_hash:
            raise RuntimeError('DNS-Zustand weicht vom geprüften Stand ab')
        call(CONTROLLER, 'POST', operations[0]['body'])
        after = call(CONTROLLER)
        verify_step(before, after, operations[0])
        if plan(after):
            raise RuntimeError('demo.home.arpa nach dem Anlegen nicht vorhanden')
    print(json.dumps({'ok': True, 'applied': bool(args.apply and operations), 'state_sha256': before_hash,
                      'state_sha256_after': state_sha256(after), 'record': RECORD,
                      'planned': [o['key'] for o in operations],
                      'existing_count': len(before), 'final_count': len(after)}))


if __name__ == '__main__':
    main()
