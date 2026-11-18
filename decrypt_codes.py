"""Decrypt codes.tsv.enc into codes.tsv using the CODES_KEY secret."""
import os
from pathlib import Path

from cryptography.fernet import Fernet

key = os.environ.get('CODES_KEY', '').strip().encode()
if not key:
    raise SystemExit('CODES_KEY secret is not set')
src = Path('codes.tsv.enc')
if not src.exists():
    raise SystemExit('codes.tsv.enc not found')
Path('codes.tsv').write_bytes(Fernet(key).decrypt(src.read_bytes()))
n = sum(1 for _ in Path('codes.tsv').open(encoding='utf-8') if _.strip())
print(f'decrypted {n} codes')
