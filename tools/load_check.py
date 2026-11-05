"""Authenticated local read load. This measures a run; it does not certify capacity."""
import argparse
import getpass
import json
import math
import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError
from tools.local_http import call, local_base, login


def run(base, username, password, role, *, concurrency=8, requests=100, paths=None):
    local_base(base)
    if not 1 <= concurrency <= 256 or not 1 <= requests <= 1000000:
        raise ValueError('concurrency 1..256; requests 1..1000000')
    auth = login(base, username, password, role)
    token = auth['token']
    try:
        volume = call(base, 'GET', '/api/dashboard-stats', token=token)['stats']
        volume = {k: volume.get(k) for k in ('elder_count', 'provider_count', 'order_count')}
    except Exception as exc:
        volume = {'unavailable': str(exc)}
    paths = paths or (['/api/orders?page=1&page_size=50', '/api/acceptance/statistics'] if role == 'admin'
                      else ['/api/orders?page=1&page_size=50', '/api/acceptance/notifications'])
    for path in paths:
        if not path.startswith('/api/') or '://' in path:
            raise ValueError('Read path must start with /api/')
    def measure(i):
        started = time.perf_counter()
        error = ''
        try:
            call(base, 'GET', paths[i % len(paths)], token=token)
        except HTTPError as exc:
            error = str(exc.code) + ': ' + exc.read(4096).decode('utf-8', errors='replace')
        except Exception as exc:
            error = str(exc)
        return (time.perf_counter() - started) * 1000, error
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(measure, range(requests)))
    duration = time.perf_counter() - started
    latencies = sorted(row[0] for row in results)
    errors = [row[1] for row in results if row[1]]
    percentile = lambda p: round(latencies[max(0, math.ceil(len(latencies) * p) - 1)], 3)
    return {'measured_at': datetime.now(timezone.utc).isoformat(), 'machine': platform.platform(),
            'python': platform.python_version(), 'processor': platform.processor(), 'base_url': base,
            'role': role, 'paths': paths, 'data_volume': volume, 'concurrency': concurrency,
            'requests': requests, 'duration_seconds': round(duration, 6), 'requests_per_second': round(requests / duration, 3),
            'latency_ms': {'min': round(latencies[0], 3), 'p50': percentile(.5), 'p95': percentile(.95), 'p99': percentile(.99), 'max': round(latencies[-1], 3)},
            'errors': len(errors), 'lock_errors': sum('locked' in e.lower() or 'busy' in e.lower() for e in errors),
            'error_groups': dict(Counter(errors)), 'conclusion': 'Measured local read workload only; 2000 simultaneous users is unverified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--role', choices=['admin', 'elder', 'provider'], required=True)
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--requests', type=int, default=100)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = run(args.base_url, args.username, getpass.getpass('Password: '), args.role, concurrency=args.concurrency, requests=args.requests)
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path
        with Path(args.output).open('x', encoding='utf-8') as stream:
            stream.write(report)
    print(report)


if __name__ == '__main__':
    main()
