"""Fetch one shard of barcodes from go-upc.com and parse the product pages.

Measured go-upc limits (GitHub runner IPs, 2026-11-18):
  * 30 search requests per fresh egress IP, then HTTP 429.
  * spacing below ~1 s also triggers a short burst 429.
So a job takes at most `--size` 30 codes, one request per `--interval` second.

Writes out/<shard>.json:
  {"shard": N, "egress": ip, "records": [{"code", "http", "sec", "result"}]}
where result.status is one of found / not_found / invalid / blocked / unknown.
Anything not found|not_found|invalid is retried by import_gh.py (left pending).
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
    for _ in range(3):
        try:
            return session.get('https://api.ipify.org', timeout=10).text.strip()
        except Exception:
            time.sleep(1)
    return ''


def fetch(session, code, interval, retries=2):
    """Return (http_status, body). Retries once after a 429 cooldown."""
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = session.get(f'https://go-upc.com/search?q={code}',
                            timeout=(10, 45))
            if r.status_code == 429 and attempt < retries:
                print(f'  429 on {code}; cooling 45s (attempt {attempt + 1})',
                      flush=True)
                time.sleep(45)
                continue
            return str(r.status_code), r.text, round(time.time() - t0, 2)
        except Exception as e:
            if attempt < retries:
                time.sleep(5)
                continue
            return 'ERR', str(e)[:150], round(time.time() - t0, 2)
    return '429', '', 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--size', type=int, default=30)
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--codes', default='codes.tsv')
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    codes_path = root / args.codes
    codes = [l.strip() for l in codes_path.read_text(encoding='utf-8').splitlines()
             if l.strip()]
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
    for i, code in enumerate(shard_codes, 1):
        status, body, sec = fetch(session, code, args.interval)
        rec = {'code': code, 'http': status, 'sec': sec, 'result': None}
        if status == '200':
            rec['result'] = parse_product(body, code)
        elif status == '400':
            rec['result'] = {'status': 'invalid', 'queried_code': code}
        elif status == '429':
            rec['result'] = {'status': 'blocked', 'queried_code': code}
        else:
            rec['result'] = {'status': 'unknown', 'queried_code': code,
                             'snippet': f'{status} {str(body)[:100]}'}
        records.append(rec)
        st = rec['result'].get('status')
        print(f'  {i:2d}/{len(shard_codes)} {code} http={status} {st} {sec}s',
              flush=True)
        if i < len(shard_codes):
            time.sleep(args.interval)

    out_dir = root / 'out'
    out_dir.mkdir(exist_ok=True)
    payload = {
        'shard': args.shard,
        'egress': ip,
        'size': args.size,
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
    print(f'shard {args.shard} done: {counts} elapsed={payload["elapsed"]}s',
          flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
