#!/usr/bin/env python3
"""
myid3_check.py — Cek massal ketersediaan SEMUA 46.656 domain 3-karakter (a-z, 0-9) .my.id.

Metode (2 fase, resumable):
  Fase 1 (cepat, gratis): kueri NS ke nameserver AUTHORITATIVE .my.id (PANDI) langsung, +norecurse.
                          NXDOMAIN  -> kandidat TERSEDIA
                          NOERROR   -> TERPAKAI (ada delegasi di zona registry)
  Fase 2 (otoritatif):    verifikasi RDAP resmi PANDI (https://rdap.pandi.id/rdap/domain/<d>)
                          HTTP 200 = terdaftar, HTTP 404 = tidak terdaftar (tersedia).
                          Sopan: ~1 req/detik + backoff otomatis kalau kena throttle.

Output (di --outdir, default ./myid3-hasil):
  semua.csv            : 46656 baris  nama,status_dns,ns,status_rdap
  kandidat_tersedia.txt: hasil fase 1 (DNS) yang belum diverifikasi RDAP
  tersedia_rdap.txt    : domain yang SUDAH dikonfirmasi RDAP 404 (siap dibeli)
  terpakai.txt         : domain terdaftar
  checkpoint_dns.jsonl / checkpoint_rdap.jsonl : titik lanjut (resume)

Contoh:
  python3 myid3_check.py --phase 1                       # sapuan penuh ~10 menit
  python3 myid3_check.py --phase 2 --limit 300            # verifikasi RDAP 300 kandidat terbaik
  python3 myid3_check.py --phase 2 --pattern 'aa1,ab1'    # verifikasi kandidat ber-pola tertentu
"""
import argparse, itertools, json, os, random, socket, string, subprocess, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# Nameserver authoritative .my.id (PANDI). e.dns.id (103.19.177.177) TIDAK dipakai: tidak menjawab.
AUTH = ["103.19.179.179", "103.19.178.178", "45.126.57.57", "203.119.112.112"]
RDAP = "https://rdap.pandi.id/rdap/domain/"
UA = {"User-Agent": "myid3-check/1.0 (kontak: nadine@mail.coblai.com)"}


def all_combos():
    chars = string.ascii_lowercase + string.digits
    return ["".join(p) for p in itertools.product(chars, repeat=3)]


# ---------- Fase 1: DNS ----------
def dns_status(dom, tries=2):
    """-> (status, ns_list). status: NXDOMAIN|NOERROR|ERROR"""
    for _ in range(tries):
        ip = AUTH[random.randrange(len(AUTH))]
        try:
            out = subprocess.run(["dig", "+time=2", "+tries=1", "+norecurse", "+noall",
                                  "+comments", "+answer", "+authority",
                                  f"@{ip}", "NS", dom], capture_output=True, text=True, timeout=6).stdout
        except Exception:
            continue
        if "status:" not in out:
            continue
        line = [l for l in out.splitlines() if "status:" in l][0]
        st = line.split("status:")[1].split(",")[0].strip()
        if st in ("NXDOMAIN", "NOERROR"):
            ns = [p[4].strip().rstrip(".") for p in (l.split() for l in out.splitlines())
                  if len(p) >= 5 and p[3] == "NS"]
            return (st, ",".join(ns[:2]))
    return ("ERROR", "")


def phase1(outdir, workers, combos):
    ck = os.path.join(outdir, "checkpoint_dns.jsonl")
    done = set()
    if os.path.exists(ck):
        for line in open(ck):
            try:
                done.add(json.loads(line)["n"])
            except Exception:
                pass
        print(f"[fase1] lanjut dari checkpoint: {len(done)} sudah selesai", flush=True)
    todo = [c for c in combos if c not in done]
    print(f"[fase1] {len(todo)} domain akan dicek ({workers} worker)", flush=True)
    lock = threading.Lock()
    t0, n = time.time(), 0
    fh = open(ck, "a", buffering=1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(dns_status, c + ".my.id"): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            st, ns = fut.result()
            fh.write(json.dumps({"n": c, "s": st, "ns": ns}) + "\n")
            with lock:
                n += 1
                if n % 2000 == 0:
                    dt = time.time() - t0
                    print(f"[fase1] {n}/{len(todo)}  {n/dt:.0f} q/s  sisa ~{(len(todo)-n)/max(n/dt,0.1)/60:.1f} menit", flush=True)
    fh.close()
    return load_checkpoint(ck)


def load_checkpoint(path):
    res = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                j = json.loads(line)
                res[j["n"]] = j
            except Exception:
                pass
    return res


# ---------- Fase 2: RDAP ----------
class RateLimiter:
    """Jaga laju + backoff/dolanan adaptif (AIMD) kalau kena throttle.

    Gagal -> laju dibelah dua. 20 sukses berturut -> laju dinaikkan 1,2x (maks = laju awal).
    """
    def __init__(self, rate, min_rate=0.2):
        self.rate, self.max_rate, self.min_rate = rate, rate, min_rate
        self.lock = threading.Lock()
        self.next = time.time()
        self.ok_streak = 0

    def wait(self):
        with self.lock:
            now = time.time()
            if now < self.next:
                time.sleep(self.next - now)
            self.next = max(time.time(), self.next) + 1.0 / self.rate

    def slow_down(self):
        with self.lock:
            self.ok_streak = 0
            self.rate = max(self.min_rate, self.rate / 2)
            print(f"[fase2] ⚠️ throttle terdeteksi -> laju turun ke {self.rate:.2f} req/s", flush=True)

    def note_ok(self):
        with self.lock:
            self.ok_streak += 1
            if self.ok_streak >= 20 and self.rate < self.max_rate:
                self.rate = min(self.max_rate, self.rate * 1.2)
                self.ok_streak = 0
                print(f"[fase2] ✓ 20 sukses berturut -> laju naik ke {self.rate:.2f} req/s", flush=True)


RDAP_EXTRA = {}   # dom -> status registry ("active", "pending delete,redemption period", ...)


def rdap_status(dom, rl, tries=4):
    for a in range(tries):
        rl.wait()
        try:
            with urllib.request.urlopen(urllib.request.Request(RDAP + dom, headers=UA), timeout=25) as r:
                try:
                    RDAP_EXTRA[dom] = ",".join(json.loads(r.read().decode("utf-8", "ignore")).get("status") or [])
                except Exception:
                    RDAP_EXTRA[dom] = ""
                rl.note_ok()
                return r.status            # 200 = terdaftar
        except urllib.error.HTTPError as e:
            if e.code == 404:
                rl.note_ok()
                return 404                 # tersedia
            if e.code in (429, 503):
                rl.slow_down(); time.sleep(5 * (a + 1)); continue
            return e.code
        except Exception:
            rl.slow_down(); time.sleep(5 * (a + 1))
    return None


def phase2(outdir, combos, rate, limit, pattern, workers):
    ck_dns = os.path.join(outdir, "checkpoint_dns.jsonl")
    if not os.path.exists(ck_dns):
        print("[fase2] checkpoint fase 1 belum ada — jalankan --phase 1 dulu", flush=True); return {}
    dns = load_checkpoint(ck_dns)
    kandidat = [n for n, v in dns.items() if v["s"] == "NXDOMAIN"]
    kandidat.sort()
    if pattern:
        pats = [p.lower() for p in pattern.split(",")]
        kandidat = [c for c in kandidat if any(c.startswith(p) for p in pats)]
    ck = os.path.join(outdir, "checkpoint_rdap.jsonl")
    done = load_checkpoint(ck)
    todo = [c for c in kandidat if c not in done]
    if limit:
        todo = todo[:limit]
    print(f"[fase2] kandidat DNS={len(kandidat)} | sudah diverifikasi={len(done)} | akan dicek={len(todo)} "
          f"| laju {rate}/s -> ~{len(todo)/rate/60:.1f} menit", flush=True)
    rl = RateLimiter(rate)
    fh = open(ck, "a", buffering=1)
    n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(rdap_status, c + ".my.id", rl): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            code = fut.result()
            fh.write(json.dumps({"n": c, "rdap": code, "status": RDAP_EXTRA.get(c + ".my.id", "")}) + "\n")
            n += 1
            if n % 50 == 0:
                print(f"[fase2] {n}/{len(todo)} (laju kini {rl.rate:.2f}/s)", flush=True)
    fh.close()
    return load_checkpoint(ck)


# ---------- Fase 3: WHOIS otoritatif (whois.id:43) ----------
WHOIS_SRV = ("45.126.59.12", 43)


def whois_query(dom, timeout=15):
    """-> (kelas, expiry, registrar). kelas: TERSEDIA|TERPAKAI|EXPIRED|ERR.

    Sumber: whois.id port 43. Terukur 11 q/s dengan 6 worker, 0 error, tanpa throttle
    (RDAP PANDI memblok IP kita setelah beberapa ratus kueri — makanya WHOIS dipakai sebagai otoritas).
    """
    try:
        with socket.create_connection(WHOIS_SRV, timeout=timeout) as s:
            s.sendall((dom + "\r\n").encode())
            buf = []
            while True:
                b = s.recv(4096)
                if not b:
                    break
                buf.append(b)
        txt = b"".join(buf).decode("utf-8", "ignore")
    except Exception:
        return ("ERR", "", "")
    if "DOMAIN NOT FOUND" in txt or "does not exist" in txt:
        return ("TERSEDIA", "", "")
    st = [l.split(":", 1)[1].strip() for l in txt.splitlines() if l.lower().startswith("domain status")]
    exp = [l.split(":", 1)[1].strip() for l in txt.splitlines() if l.lower().startswith("registry expiry")]
    reg = [l.split(":", 1)[1].strip() for l in txt.splitlines() if l.lower().startswith("registrar:")]
    if any("pendingDelete" in x or "redemption" in x for x in st):
        return ("EXPIRED", exp[0][:10] if exp else "", reg[0] if reg else "")
    return ("TERPAKAI", exp[0][:10] if exp else "", reg[0] if reg else "")


def phase3(outdir, combos, workers):
    ck = os.path.join(outdir, "checkpoint_whois.jsonl")
    done = set(load_checkpoint(ck))
    todo = [c for c in combos if c not in done]
    print(f"[fase3] {len(todo)} domain via WHOIS ({workers} worker) — {len(done)} sudah selesai", flush=True)
    lock, n, t0 = threading.Lock(), 0, time.time()
    fh = open(ck, "a", buffering=1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(whois_query, c + ".my.id"): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            kls, exp, reg = fut.result()
            fh.write(json.dumps({"n": c, "k": kls, "exp": exp, "reg": reg}) + "\n")
            with lock:
                n += 1
                if n % 500 == 0:
                    dt = time.time() - t0
                    print(f"[fase3] {n}/{len(todo)}  {n/dt:.1f} q/s  sisa ~{(len(todo)-n)/max(n/dt,0.1)/60:.1f} menit", flush=True)
    fh.close()
    return load_checkpoint(ck)


# ---------- Laporan ----------
def write_reports(outdir, dns, rdap, whois=None):
    whois = whois or {}
    semua = os.path.join(outdir, "semua.csv")
    with open(semua, "w") as f:
        f.write("domain,status_dns,nameserver,status_rdap,status_registry,status_whois,expiry,registrar\n")
        for c in sorted(dns):
            r = rdap.get(c, {}).get("rdap")
            st = rdap.get(c, {}).get("status", "")
            w = whois.get(c, {})
            f.write(f"{c}.my.id,{dns[c]['s']},{dns[c]['ns']},{r if r else ''},{st},"
                    f"{w.get('k','')},{w.get('exp','')},{w.get('reg','')}\n")
    tersedia_dns = sorted(c for c, v in dns.items() if v["s"] == "NXDOMAIN")
    open(os.path.join(outdir, "kandidat_tersedia.txt"), "w").write("".join(f"{c}.my.id\n" for c in tersedia_dns))
    tersedia_rdap = sorted(c for c, v in rdap.items() if v.get("rdap") == 404)
    open(os.path.join(outdir, "tersedia_rdap.txt"), "w").write("".join(f"{c}.my.id\n" for c in tersedia_rdap))
    pending = sorted(c for c, v in rdap.items() if v.get("rdap") == 200 and
                     ("pending delete" in (v.get("status") or "") or "redemption" in (v.get("status") or "")))
    open(os.path.join(outdir, "pending_delete.txt"), "w").write("".join(f"{c}.my.id\n" for c in pending))
    terpakai = sorted(c for c, v in dns.items() if v["s"] == "NOERROR")
    open(os.path.join(outdir, "terpakai.txt"), "w").write("".join(f"{c}.my.id\n" for c in terpakai))
    # hasil WHOIS (otoritatif)
    n_w = 0
    if whois:
        tersedia_w = sorted(c for c, v in whois.items() if v.get("k") == "TERSEDIA")
        open(os.path.join(outdir, "tersedia_whois.txt"), "w").write("".join(f"{c}.my.id\n" for c in tersedia_w))
        expired_w = sorted(c for c, v in whois.items() if v.get("k") == "EXPIRED")
        open(os.path.join(outdir, "expired_pending_delete.txt"), "w").write("".join(f"{c}.my.id\n" for c in expired_w))
        n_w = len(tersedia_w)
    return semua, len(tersedia_dns), len(tersedia_rdap), len(terpakai), n_w


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", default="1", choices=["1", "2", "3", "12", "13", "123"])
    p.add_argument("--whois-workers", type=int, default=6, help="fase3: paralel WHOIS (default 6 = ~11 q/s)")
    p.add_argument("--outdir", default="myid3-hasil")
    p.add_argument("--workers", type=int, default=60, help="fase1: paralel dig (default 60)")
    p.add_argument("--rate", type=float, default=1.0, help="fase2: req/detik ke RDAP (default 1.0)")
    p.add_argument("--rdap-workers", type=int, default=1, help="fase2: paralel RDAP (default 1, sopan)")
    p.add_argument("--limit", type=int, default=0, help="fase2: batasi jumlah (0=semua)")
    p.add_argument("--pattern", default="", help="fase2: hanya kandidat berawalan pola ini, mis. 'aa,ab'")
    p.add_argument("--sample", type=int, default=0, help="fase1: hanya uji N acak")
    a = p.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    combos = all_combos()
    if a.sample:
        random.seed(42); combos = sorted(random.sample(combos, a.sample))

    if a.phase in ("1", "12"):
        t0 = time.time()
        dns = phase1(a.outdir, a.workers, combos)
        print(f"[fase1] selesai dalam {(time.time()-t0)/60:.1f} menit — {len(dns)} domain terdata", flush=True)
    if a.phase in ("2", "12"):
        phase2(a.outdir, combos, a.rate, a.limit, a.pattern, a.rdap_workers)
    if a.phase in ("3", "13", "123"):
        t0 = time.time()
        phase3(a.outdir, combos, a.whois_workers)
        print(f"[fase3] selesai dalam {(time.time()-t0)/60:.1f} menit", flush=True)

    dns = load_checkpoint(os.path.join(a.outdir, "checkpoint_dns.jsonl"))
    rdap = load_checkpoint(os.path.join(a.outdir, "checkpoint_rdap.jsonl"))
    whois = load_checkpoint(os.path.join(a.outdir, "checkpoint_whois.jsonl"))
    semua, n_dns, n_rdap, n_pakai, n_whois = write_reports(a.outdir, dns, rdap, whois)
    print(f"\n=== HASIL ({a.outdir}) ===")
    print(f"  total dicek            : {len(dns)}")
    print(f"  TERPAKAI (DNS)         : {n_pakai}")
    print(f"  kandidat tersedia (DNS): {n_dns}  -> kandidat_tersedia.txt")
    pending = [c for c, v in rdap.items() if v.get("rdap") == 200 and
               ("pending delete" in (v.get("status") or "") or "redemption" in (v.get("status") or ""))]
    if rdap:
        print(f"  tersedia TERVERIFIKASI RDAP: {n_rdap}  -> tersedia_rdap.txt")
        print(f"  (catatan: {len(pending)} terdaftar tapi expired/pending-delete -> pending_delete.txt)")
    if whois:
        from collections import Counter as _C
        c = _C(v["k"] for v in whois.values())
        print(f"  WHOIS (otoritatif, {len(whois)} domain): {dict(c)}")
        print(f"  TERSEDIA PASTI        : {n_whois}  -> tersedia_whois.txt")
    print(f"  rincian lengkap        : {semua}")


if __name__ == "__main__":
    main()
