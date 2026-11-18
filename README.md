# barcode product lookups on GitHub runners

Pulls product pages from go-upc.com and stores them as JSON. Each job uses one
fresh runner egress IP, so it stays inside the per-IP allowance (30 searches).

```
shard N = codes[N*size : (N+1)*size]
```

`codes.tsv.enc` is an encrypted barcode list; the workflow decrypts it with the
`CODES_KEY` repository secret.

## Dispatch

```bash
gh workflow run scrape.yml -f shards='[0,1,2]' -f size=30 -f interval=1.0
```

## Output

One artifact per shard plus a merged `batch` artifact per run:

```json
{"code": "5449000000996", "http": "200",
 "result": {"status": "found", "name": "...", "brand": "...", "images": ["..."]}}
```

`status` is `found`, `not_found`, `invalid`, `blocked` or `unknown`. Only the
first three are final; the rest stay queued for another pass.
