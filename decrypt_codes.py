"""Decrypt an encrypted code list using the CODES_KEY secret.

Defaults to codes.tsv.enc -> codes.tsv (the go-upc queue).  Pass --src/--dst
to decrypt the upcitemdb queue instead.
"""
import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet

ap = argparse.ArgumentParser()
ap.add_argument('--src', default='codes.tsv.enc')
ap.add_argument('--dst', default='codes.tsv')
args = ap.parse_args()

key = os.environ.get('CODES_KEY', '').strip().encode()
if not key:
    raise SystemExit('CODES_KEY secret is not set')
src = Path(args.src)
if not src.exists():
    raise SystemExit(f'{src} not found')
Path(args.dst).write_bytes(Fernet(key).decrypt(src.read_bytes()))
n = sum(1 for _ in Path(args.dst).open(encoding='utf-8') if _.strip())
print(f'decrypted {n} codes from {src}')
