"""Fetch a shard of barcodes from upcitemdb.com product pages.

upcitemdb serves one product page per barcode at /upc/<code> and applies a
daily per-IP cap (~100 pages, then 429 "Exceed Daily Limit" for the rest of
the day).  A GitHub runner gets a fresh egress IP per job, so one job can
safely spend up to ~100 requests.

Writes out/<shard>.json with the same envelope as scrape_shard.py:
  {"shard": N, "egress": ip, "records": [{"code", "http", "sec", "result"}]}
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive',
}

import html as htmllib


def clean(fragment):
    if not fragment:
        return ''
    s = re.sub(r'<[^>]+>', ' ', fragment)
    s = htmllib.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def parse_page(raw, code):
    title = ''
    m = re.search(r'<title>(.*?)</title>', raw, re.S | re.I)
    if m:
        title = clean(m.group(1))
        title = re.sub(r'\s*\|\s*upcitemdb\.com\s*$', '', title, flags=re.I)
        title = re.sub(r'^(?:UPC|EAN)\s+\d+\s*-\s*', '', title).strip()

    category = ''
    m = re.search(r'<li class="breadcrumb-item active"[^>]*>(.*?)</li>',
                  raw, re.S | re.I)
    if m:
        category = clean(m.group(1))

    fields = {}
    for label in ('Brand', 'Model #', 'Size', 'Color', 'Weight',
                  'Product Dimension', 'Country of Registration',
                  'Amazon ASIN', 'Last Scanned'):
        m = re.search(r'<td>\s*' + re.escape(label) + r'\s*:?\s*</td>\s*<td>(.*?)</td>',
                      raw, re.S | re.I)
        if m:
            fields[label] = clean(m.group(1))

    description = ''
    m = re.search(r'<div class="destxt">(.*?)</div>', raw, re.S | re.I)
    if m:
        description = clean(m.group(1))
    if not description:
        for m in re.finditer(r'<small>(.*?)</small>', raw, re.S | re.I):
            t = clean(m.group(1))
            if len(t) > 20 and not t.lower().startswith(('upc ', 'ean ')):
                description = t
                break

    images = []
    for m in re.finditer(r'<img[^>]+>', raw, re.I):
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag, re.I)
        alt = re.search(r'alt="([^"]*)"', tag, re.I)
        if not src:
            continue
        u = htmllib.unescape(src.group(1))
        if u.startswith('//'):
            u = 'https:' + u
        if not u.startswith('http'):
            continue
        if re.search(r'/static/|resize\.jpg|placeholder|noimage|spacer|blank\.',
                     u, re.I):
            continue
        if '/barcode/' in u or re.search(r'logo|icon|sprite|badge', u, re.I):
            continue
        alt_text = (alt.group(1) if alt else '').lower()
        if 'product image' not in alt_text and 'class="product' not in tag.lower():
            continue
        if u not in images:
            images.append(u)

    expired = 'Exceed Daily Limit' in raw or 'Too Many Requests' in raw
    found = (not expired and
             ('class="detail-list"' in raw or bool(category) or bool(title)))

    return {
        'status': 'found' if found else ('blocked' if expired else 'not_found'),
        'queried_code': code,
        'title': title,
        'brand': fields.get('Brand', ''),
        'category': category,
        'description': description,
        'model': fields.get('Model #', ''),
        'size': fields.get('Size', ''),
        'color': fields.get('Color', ''),
        'weight': fields.get('Weight', ''),
        'dimension': fields.get('Product Dimension', ''),
        'country': fields.get('Country of Registration', ''),
        'images': images,
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
    ap.add_argument('--interval', type=float, default=2.0)
    ap.add_argument('--codes', default='upc_codes.tsv')
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

    for i, code in enumerate(shard_codes, 1):
        if stop_reason:
            records.append({'code': code, 'http': None, 'sec': 0,
                            'result': {'status': 'blocked',
                                       'queried_code': code}})
            continue

        sec = 0.0
        status = 'ERR'
        body = ''
        for attempt in (1, 2):
            t0 = time.time()
            try:
                r = session.get(f'https://www.upcitemdb.com/upc/{code}',
                                timeout=(6, 25))
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
            rec['result'] = parse_page(body, code)
            if rec['result']['status'] == 'blocked':
                stop_reason = 'quota'
        elif status == '429':
            rec['result'] = {'status': 'blocked', 'queried_code': code}
            stop_reason = 'quota'
        elif status == '404':
            rec['result'] = {'status': 'not_found', 'queried_code': code}
        else:
            rec['result'] = {'status': 'failed', 'queried_code': code,
                             'snippet': f'{status} {str(body)[:100]}'}

        records.append(rec)
        print(f'  {i:2d}/{len(shard_codes)} {code} http={status} '
              f"{rec['result'].get('status')} {sec}s", flush=True)
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
          f"elapsed={payload['elapsed']}s", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
