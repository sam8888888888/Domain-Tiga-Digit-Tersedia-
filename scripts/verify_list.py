#!/usr/bin/env python3
"""verify_list.py — verifikasi RDAP untuk daftar nama pilihan (shortlist premium).

Pakai: python3 verify_list.py /opt/data/myid3-hasil/shortlist_premium.txt [rate]
Hasil ditambahkan ke /opt/data/myid3-hasil/checkpoint_rdap.jsonl (dipakai laporan utama).
"""
import json, os, sys, time
sys.path.insert(0, "/opt/data/scripts")
import myid3_check as m

OUT = "/opt/data/myid3-hasil"
path = sys.argv[1]
rate = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

names = [l.strip().replace(".my.id", "") for l in open(path) if l.strip() and not l.startswith("#")]
done = set(m.load_checkpoint(f"{OUT}/checkpoint_rdap.jsonl"))
# skip yang sudah terverifikasi & tahu statusnya
sudah = {}
for line in open(f"{OUT}/checkpoint_rdap.jsonl"):
    j = json.loads(line)
    if j.get("rdap") in (200, 404):
        sudah[j["n"]] = j["rdap"]
todo = [n for n in names if n not in sudah]
print(f"shortlist={len(names)} | sudah terverifikasi={len(names)-len(todo)} | akan dicek={len(todo)}", flush=True)

rl = m.RateLimiter(rate)
fh = open(f"{OUT}/checkpoint_rdap.jsonl", "a", buffering=1)
hasil = {}
for i, n in enumerate(todo, 1):
    code = m.rdap_status(n + ".my.id", rl)
    fh.write(json.dumps({"n": n, "rdap": code, "status": m.RDAP_EXTRA.get(n + ".my.id", "")}) + "\n")
    if code in (200, 404):
        hasil[n] = code
        print(f"  {'✅ TERSEDIA ' if code==404 else '❌ TERPAKAI '} {n}.my.id", flush=True)
    else:
        print(f"  ⚠️  gagal ({code}) {n}.my.id", flush=True)
print(f"\nselesai: tersedia {sum(1 for v in hasil.values() if v==404)} / terpakai {sum(1 for v in hasil.values() if v==200)}"
      f" / gagal {len(todo)-len(hasil)}", flush=True)
