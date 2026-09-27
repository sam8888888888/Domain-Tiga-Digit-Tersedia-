# Domain Tiga Digit Tersedia — `.my.id` (a-z, 0-9)

Hasil pengecekan **massal seluruh 46.656 kombinasi domain 3-karakter** (`a-z` + `0-9`) pada TLD **`.my.id`**, diverifikasi langsung ke registry.

- **Tanggal cek:** 26–27 September 2026 (CEST)
- **Cakupan:** 36³ = **46.656 domain**, tuntas 100%, **0 error**
- **Sumber otoritatif:** WHOIS registry `.id` (`whois.id:43`) — bukan tebakan DNS

## Hasil

| Status | Jumlah | Arti |
|---|---:|---|
| ✅ **TERSEDIA** | **38.811** | tidak terdaftar → **bisa langsung didaftarkan** |
| ❌ TERPAKAI | 7.555 | aktif terdaftar (registrar + tanggal expiry ada di `data/semua-46656.csv`) |
| ⏳ EXPIRED / pending-delete | 290 | terdaftar tapi sudah kedaluwarsa → **belum bisa didaftarkan**, tunggu domain dilepas |

## Persentase tersedia per pola

| Pola | Contoh | Tersedia | % |
|---|---|---:|---:|
| angka-angka-huruf | `12a` | 2.584 / 2.600 | 99,4% |
| angka-huruf-huruf | `1ab` | 6.692 / 6.760 | 99,0% |
| angka-huruf-angka | `1a2` | 2.572 / 2.600 | 98,9% |
| huruf-angka-huruf | `a1b` | 6.620 / 6.760 | 97,9% |
| huruf-huruf-angka | `ab1` | 6.531 / 6.760 | 96,6% |
| huruf-angka-angka | `a12` | 2.499 / 2.600 | 96,1% |
| angka murni | `123` | 867 / 1.000 | 86,7% |
| **huruf murni** | `abc` | 10.446 / 17.576 | **59,4%** |

## Isi repo

| File | Isi |
|---|---|
| `data/tersedia_whois.txt` | **38.811** domain tersedia (semua pola) |
| `data/tersedia_huruf_saja.txt` | **10.446** domain tersedia **tanpa angka** (huruf murni) |
| `data/tersedia_ada_angka.txt` | **28.365** domain tersedia yang mengandung angka |
| `data/expired_pending_delete.txt` | 290 domain watchlist (expired / redemption) |
| `data/terpakai.txt` | 7.555 domain aktif terdaftar |
| `data/semua-46656.csv` | **46.656 baris** — status DNS, status WHOIS, registrar, expiry |
| `data/rekomendasi.txt` | kurasi: kata bermakna, deret alfabet, pola cermin + watchlist |
| `scripts/myid3_check.py` | skrip pengecekan (fase 1 DNS, fase 3 WHOIS, resumable) |
| `scripts/verify_list.py` | verifikasi daftar nama pilihan via RDAP |

## Metode

1. **Fase 1 — sapuan DNS (filter cepat).** Kueri `NS` langsung ke nameserver authoritative zona `.my.id` dengan `dig +norecurse` (tanpa resolusi nama NS, pakai IP: `103.19.179.179`, `103.19.178.178`, `45.126.57.57`, `203.119.112.112`; `e.dns.id`/`103.19.177.177` tidak menjawab — dibuang). `NXDOMAIN` = kandidat kosong, `NOERROR` = ada delegasi.
   → 46.656 domain selesai **< 2 menit** (50 worker, ~570 kueri/detik, 0 error).
2. **Fase 3 — verifikasi WHOIS (otoritatif).** Setiap domain dicek ke `whois.id:43`:
   - `DOMAIN NOT FOUND` → **TERSEDIA**
   - `Domain Status: pendingDelete` / `redemptionPeriod` → **EXPIRED** (watchlist)
   - selain itu → **TERPAKAI**
   → 46.656 domain selesai **51 menit** (8 worker, 15,2 kueri/detik, 0 error).

### Kenapa bukan RDAP?

RDAP resmi PANDI (`https://rdap.pandi.id/rdap/domain/<domain>`) **memblokir IP** setelah beberapa ratus kueri (koneksi di-reset, `HTTP=000`, blokir puluhan menit). Tidak layak untuk sapuan massal. WHOIS registry justru stabil tanpa throttle.

### Akurasi metode DNS (diukur pada seluruh populasi, bukan asumsi)

- Kandidat `NXDOMAIN`: **2,11% salah** (838 dari 39.641) → 554 ternyata masih aktif terdaftar, 284 ternyata expired.
- Domain `NOERROR`: **0,10% salah** (7 dari 7.007).

→ Sapuan DNS bagus untuk memangkas 46 ribu jadi ratusan/ribuan kandidat, **tidak cukup** sebagai daftar final. Verifikasi WHOIS wajib.

## Cara reproduksi

```bash
python3 scripts/myid3_check.py --phase 1 --workers 50 --outdir hasil   # sapuan DNS (±2 menit)
python3 scripts/myid3_check.py --phase 3 --whois-workers 8 --outdir hasil  # verifikasi WHOIS (±51 menit)
```

Skrip menulis checkpoint `JSONL` sehingga bisa dilanjutkan bila terputus. Output: `tersedia_whois.txt`, `expired_pending_delete.txt`, `terpakai.txt`, `semua.csv`.

## Catatan penting

- Status "tersedia" di sini = **tidak ada di registry**. Kata akhir tetap di **checkout registrar**: nama bisa berstatus *reserved* oleh PANDI atau berubah status kapan saja.
- Domain dengan status **EXPIRED/pending-delete TIDAK bisa didaftarkan** sampai PANDI melepasnya; itu "watchlist", bukan stok siap beli.
- Nama-nama bagus (bermakna, pendek, mudah diucapkan) jauh lebih sering berstatus expired daripada rata-rata — beberapa nama premium seperti `sam.my.id`, `cat.my.id`, `dog.my.id`, `key.my.id`, `bug.my.id` ada di daftar watchlist, bukan di daftar tersedia.
