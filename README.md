# Data Jemaat Gereja — Direktori Keluarga Jemaat

Aplikasi web direktori keluarga untuk satu gereja. **Admin gereja** (satu
akun, dilindungi login) mengelola data keluarga jemaat: kepala keluarga,
pasangan, status & tanggal perkawinan, foto pernikahan, dan anak-anak
beserta fotonya. **Jemaat** tidak perlu login — mereka cukup mengecek data
keluarganya dari halaman publik.

Ini proyek kode mandiri yang bisa di-deploy sendiri. Proyek ini **belum**
di-deploy ke layanan publik mana pun — keputusan hosting ada di tangan Anda.

## Cara jemaat mengecek data (halaman publik)

Ada dua cara pencarian, dengan tingkat keterbukaan yang **sengaja berbeda**:

1. **Cari dengan NIK** — masukkan NIK (tepat 16 digit) milik kepala
   keluarga **atau** pasangan. Bila NIK tepat, tampil **kartu keluarga
   lengkap**: foto pernikahan, status perkawinan, tanggal menikah, nama
   kepala keluarga, nama pasangan, dan daftar anak (**Anak 1, Anak 2,
   ...**) beserta foto tiap anak bila ada.
2. **Cari dengan nama** — ketik sebagian nama. Hasilnya hanya **daftar
   singkat**: nama kepala keluarga, nama pasangan, dan status perkawinan.
   Setiap hasil mengarahkan pengunjung untuk memasukkan NIK keluarga itu
   bila ingin melihat detail lengkap.

> **Pengaman privasi (penting):** pencarian nama **tidak pernah**
> menampilkan foto keluarga, daftar anak, tanggal menikah, atau NIK.
> Detail lengkap dan foto keluarga **hanya** bisa dibuka dengan NIK yang
> tepat. NIK juga tidak ditampilkan di halaman publik mana pun.
> (Satu-satunya gambar yang selalu tampil di halaman publik adalah banner
> & wallpaper gereja yang memang dipasang admin untuk umum — bukan foto
> keluarga.)

## Fitur

- **Publik (tanpa login)**: pencarian NIK → kartu keluarga lengkap;
  pencarian nama → daftar minimal; pesan ramah bila data tidak ditemukan.
- **Admin (satu akun, wajib login)**:
  - CRUD keluarga: nama + NIK kepala keluarga (unik, tepat 16 digit),
    nama + NIK pasangan (opsional), status perkawinan (`Menikah`,
    `Belum Menikah`, `Cerai Hidup`, `Cerai Mati`), tanggal menikah,
    unggah/ganti/hapus **foto pernikahan**.
  - Kelola **anak** per keluarga: tambah/ubah/hapus, **atur urutan**
    (tombol Naik/Turun menentukan label Anak 1, 2, 3, ...), unggah foto
    tiap anak.
  - Dashboard dengan pencarian keluarga (nama/NIK) dan ringkasan jumlah.
  - **Pengaturan**: nama gereja (tampil di kepala halaman & judul halaman
    publik), **banner** halaman publik, **wallpaper/background** halaman
    publik (unggah/ganti/hapus; bila kosong, halaman memakai tampilan
    bawaan berkepala & berlatar polos), dan ganti password admin.
- **Foto**: format JPG/JPEG/PNG/WebP, maks ±5 MB per berkas. Semua foto —
  foto nikah, foto anak, banner, wallpaper — diunggah oleh admin sendiri.
- Password tersimpan sebagai **hash** (PBKDF2-HMAC-SHA256 + salt),
  session login lewat cookie HttpOnly.
- UI Bahasa Indonesia, ramah HP.

## Stack

- Python 3 (diuji di Python 3.12) — backend **hanya standard library**
  (`http.server`, `sqlite3`), tanpa framework dan **tanpa paket eksternal
  sama sekali** (`requirements.txt` hanya berisi komentar). Parser
  `multipart/form-data` untuk unggahan foto ditulis sendiri dengan
  standard library.
- Database: **SQLite** satu file (`data/jemaat.db`, dibuat otomatis).
- Foto tersimpan sebagai berkas di `data/uploads/` (lihat bagian
  Keamanan foto).

## Menjalankan secara lokal (perintah persis)

```bash
cd ~/workspace/data-jemaat-gereja
python3 app.py
```

Lalu buka **http://127.0.0.1:8000** di browser (halaman publik).
Halaman admin di **http://127.0.0.1:8000/login**.

Pengaturan lewat environment variable:

| Variable | Default | Keterangan |
|---|---|---|
| `PORT` | `8000` | Port server (server mendengarkan `0.0.0.0`) |
| `JEMAAT_DB` | `data/jemaat.db` | Lokasi file database SQLite (folder dibuat otomatis) |
| `JEMAAT_UPLOADS` | `data/uploads/` | Folder berkas foto (dibuat otomatis) |

Contoh: `PORT=8080 JEMAAT_DB=/srv/jemaat.db JEMAAT_UPLOADS=/srv/uploads python3 app.py`

## Akun admin awal

Saat database masih kosong, aplikasi membuat **satu akun admin awal**:

- Username: `admin`
- Password: `admin123`

> **WAJIB:** akun ini hanya untuk pengujian lokal. **Ganti password-nya
> segera** lewat menu **Pengaturan → Ganti Password Admin** setelah login
> pertama, dan **jangan pernah memakai akun/password awal ini di
> produksi**. Dashboard admin juga menampilkan peringatan selama password
> awal belum diganti. Hanya admin yang bisa login — jemaat tidak punya
> akun dan tidak membutuhkannya.

Alur pemakaian singkat:

1. Login sebagai admin → menu **Pengaturan**: isi nama gereja, dan bila
   mau, unggah banner & wallpaper halaman publik.
2. Menu **Tambah Keluarga**: isi data kepala keluarga + pasangan, status,
   tanggal menikah, dan foto pernikahan.
3. Di halaman keluarga itu, tambahkan anak-anaknya (nama + foto), atur
   urutannya dengan tombol Naik/Turun.
4. Jemaat membuka halaman publik, memasukkan NIK, dan melihat kartu
   keluarganya.

## Struktur data (SQLite, `data/jemaat.db`)

| Tabel | Isi |
|---|---|
| `users` | akun admin: `username` (unik), `password_hash`, `salt`, `nama` |
| `settings` | pengaturan kunci-nilai: `nama_gereja`, `banner_foto`, `wallpaper_foto` (dua terakhir berisi nama berkas foto atau kosong) |
| `families` | keluarga: `kepala_nama`, `kepala_nik` (unik, 16 digit), `pasangan_nama`, `pasangan_nik` (boleh kosong), `status`, `tanggal_menikah`, `foto_nikah` (nama berkas atau kosong) |
| `children` | anak: `family_id`, `nama`, `foto` (nama berkas atau kosong), `urutan` |

Session login tersimpan di memori server (bukan di tabel), jadi me-restart
server akan mengeluarkan (logout) admin. Itu disengaja untuk versi
sederhana ini.

## Keamanan foto

- Foto disimpan di `data/uploads/` (atau `JEMAAT_UPLOADS`) dengan **nama
  berkas token acak** (24 karakter hex + ekstensi), bukan nama asli
  unggahan — jadi alamat foto tidak bisa ditebak dari nama orang.
- Berkas **hanya** disajikan lewat route aplikasi `GET /foto/<nama>`,
  yang hanya menerima nama berpola token tersebut. Tidak ada directory
  listing, dan percobaan path traversal ditolak (404).
- Mengganti/menghapus foto atau menghapus keluarga/anak akan menghapus
  berkas foto lamanya dari disk.

## Menguji

`test_alur.py` menjalankan sendiri server uji pada port sementara dengan
database & folder unggahan sementara, lalu memeriksa seluruh alur:

```bash
cd ~/workspace/data-jemaat-gereja
python3 test_alur.py        # akhiri dengan "SEMUA UJI LULUS"
```

Yang dibuktikan: halaman publik & login tampil (200); halaman admin tanpa
login diarahkan ke login; login admin; buat keluarga + unggah foto nikah
dan ambil kembali berkasnya; NIK duplikat ditolak; tambah dua anak (satu
berfoto, fotonya terambil utuh); pencarian NIK (kepala & pasangan) →
kartu lengkap berisi nama anak & foto; pencarian nama → hanya info
minimal (tes menegaskan **tidak ada** URL foto, nama anak, NIK, maupun
tanggal menikah pada responsnya); NIK tak dikenal → pesan ramah;
pengaturan nama gereja tampil di halaman publik; unggah **banner** dan
**wallpaper** → keduanya tersaji lewat `/foto/` dan dirujuk halaman
publik, dan menghapus banner mengembalikan tampilan bawaan.

## Panduan deploy ke hosting (belum dilakukan — lakukan sendiri)

Pola deploy-nya sama seperti aplikasi Absen Siswa Multi-Guru: unggah
proyek ke **GitHub**, lalu pasang dari repository itu ke **Railway** atau
**Render**. Berkas bantu sudah disiapkan: `render.yaml` (blueprint Render:
region Singapore, paket Free, build `pip install -r requirements.txt`,
start `python3 app.py`, health check `/login`), `Procfile`,
`.python-version`, dan `runtime.txt` (Python 3.12). Aplikasi membaca
`PORT`, `JEMAAT_DB`, dan `JEMAAT_UPLOADS` dari environment, jadi tidak
perlu mengubah kode.

Langkah ringkas:

1. Buat repository GitHub baru, unggah seluruh isi folder proyek ini.
   Folder `data/` (database + foto lokal Anda) **tidak** ikut terunggah
   karena dikecualikan oleh `.gitignore`.
2. Di Railway/Render, buat Web Service dari repository itu.
3. Login `admin` / `admin123`, **langsung ganti password**, isi nama
   gereja, dan mulai masukkan data.

> #### ⚠️ Paket gratis = hanya untuk tes
>
> Paket gratis Railway/Render umumnya **tidak menyediakan penyimpanan
> persisten**: file database `data/jemaat.db` **dan semua foto** di
> `data/uploads/` bisa **terhapus** setiap aplikasi restart/deploy ulang,
> dan aplikasi kembali seperti baru (login awal berlaku lagi). Gunakan
> paket gratis hanya untuk mencoba alur & tampilan.
>
> Untuk pemakaian resmi, pakai paket berbayar **dengan disk/volume
> persisten**, lalu arahkan database dan foto ke disk itu, contoh:
>
> - Render (Starter + Disk 1 GB terpasang di `/var/data`):
>   `JEMAAT_DB=/var/data/jemaat.db` dan `JEMAAT_UPLOADS=/var/data/uploads`
> - Railway (Hobby + Volume terpasang di `/data`):
>   `JEMAAT_DB=/data/jemaat.db` dan `JEMAAT_UPLOADS=/data/uploads`
>
> Karena sesi login disimpan di memori satu proses, jalankan **satu
> instance saja**. **Backup berkala** file `jemaat.db` **dan** folder
> `uploads` — keduanya bersama-sama adalah seluruh data jemaat. Data NIK
> dan foto keluarga adalah data pribadi: amankan akses servernya dan
> jangan sebarkan alamat admin.

## Keterbatasan yang diketahui

- Satu akun admin saja (belum ada peran pengurus/viewer terpisah).
- Belum ada: impor data massal (CSV/Excel), lupa/reset password lewat
  email, audit log, dan pencarian publik dibatasi laju (rate limit) —
  halaman publik sebaiknya tidak diindeks mesin pencari; pertimbangkan
  perlindungan tambahan dari platform hosting bila datanya sensitif.
- Foto tidak diperkecil/dikompres di server; foto HP berukuran besar
  akan ditolak bila melebihi 5 MB — kecilkan dulu sebelum mengunggah.
