"""Fetch one shard of barcodes from go-upc.com and parse the product pages.

Measured go-upc limits (GitHub runner IPs, 2026-11-18):
  * a fresh egress IP gets ~30 search requests, some pooled runner IPs fewer;
  * spacing below ~1 s also triggers a short burst 429;
  * a quota 429 does not clear within 45 s, so retrying it only burns the slot.

A job therefore aborts as soon as the IP shows it is exhausted, and marks the
codes it did not reach as `blocked`, which leaves them pending for the next
pass.

Writes out/<shard>.json:
  {"shard": N, "egress": ip, "records": [{"code", "http", "sec", "result"}]}
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from parse_go_upc import parse_product

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive',
}


def egress_ip(session):
    for _ in range(2):
        try:
            return session.get('https://api.ipify.org', timeout=8).text.strip()
        except Exception:
            time.sleep(1)
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--size', type=int, default=30)
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--codes', default='codes.tsv')
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    codes = [l.strip() for l in (root / args.codes).read_text(encoding='utf-8')
             .splitlines() if l.strip()]
    start = args.shard * args.size
    shard_codes = codes[start:start + args.size]
    if not shard_codes:
        print(f'shard {args.shard}: no codes (past end of {len(codes)})')
        return 0

    session = requests.Session()
    session.headers.update(HEADERS)
    ip = egress_ip(session)
    print(f'shard {args.shard}: {len(shard_codes)} codes, egress {ip}', flush=True)

    records = []
    t_start = time.time()
    stop_reason = None
    conn_fail = 0

    for i, code in enumerate(shard_codes, 1):
        if stop_reason:
            records.append({'code': code, 'http': None,
                            'result': {'status': 'blocked',
                                       'queried_code': code}})
            continue

        sec = 0.0
        status = 'ERR'
        body = ''
        for attempt in (1, 2):
            t0 = time.time()
            try:
                r = session.get(f'https://go-upc.com/search?q={code}',
                                timeout=(6, 20))
                sec = round(time.time() - t0, 2)
                status, body = str(r.status_code), r.text
                break
            except Exception as e:
                sec = round(time.time() - t0, 2)
                status = 'ERR'
                body = str(e)[:150]
                if attempt == 1:
                    time.sleep(2)

        rec = {'code': code, 'http': status, 'sec': sec, 'result': None}
        if status == '200':
            rec['result'] = parse_product(body, code)
            conn_fail = 0
        elif status == '400':
            rec['result'] = {'status': 'invalid', 'queried_code': code}
            conn_fail = 0
        elif status == '429':
            rec['result'] = {'status': 'blocked', 'queried_code': code}
            stop_reason = 'quota'
        else:
            rec['result'] = {'status': 'unknown', 'queried_code': code,
                             'snippet': f'{status} {str(body)[:100]}'}
            conn_fail += 1
            if conn_fail >= 2:
                stop_reason = 'connection'

        records.append(rec)
        st = rec['result'].get('status')
        print(f'  {i:2d}/{len(shard_codes)} {code} http={status} {st} {sec}s',
              flush=True)
        if stop_reason:
            print(f'  stop: {stop_reason}; remaining codes left pending',
                  flush=True)
        elif i < len(shard_codes):
            time.sleep(args.interval)

    out_dir = root / 'out'
    out_dir.mkdir(exist_ok=True)
    payload = {
        'shard': args.shard,
        'egress': ip,
        'size': args.size,
        'stop': stop_reason,
        'elapsed': round(time.time() - t_start, 1),
        'started': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'records': records,
    }
    (out_dir / f'{args.shard}.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    counts = {}
    for r in records:
        s = (r['result'] or {}).get('status', 'none')
        counts[s] = counts.get(s, 0) + 1
    print(f'shard {args.shard} done: {counts} stop={stop_reason} '
          f'elapsed={payload["elapsed"]}s', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
