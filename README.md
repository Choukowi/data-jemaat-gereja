# Data Jemaat Gereja — Direktori Keluarga Jemaat

Aplikasi web direktori keluarga untuk satu gereja. **Admin gereja** (satu
akun, dilindungi login) mengelola data keluarga jemaat: kepala keluarga,
pasangan, status & tanggal perkawinan, foto pernikahan, anak-anak beserta
fotonya, dan **dokumen resmi** keluarga (akta nikah, akta kelahiran, surat
baptis). **Jemaat** bisa mengecek data keluarganya dari halaman publik
tanpa login, dan — bila admin mengaktifkan **Login Jemaat** untuk
keluarganya — bisa masuk untuk melihat kartu keluarganya dan **mengunduh
dokumennya sendiri**.

Tampilan aplikasi memakai kerangka modern (sidebar di layar lebar, bilah
atas + navigasi bawah di HP) dengan palet khas gereja: **ungu tua + aksen
emas** — sengaja berbeda dari aplikasi Absen. Nama gereja, **logo**,
banner, dan wallpaper semuanya bisa diatur admin dari menu Pengaturan.

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
> (Satu-satunya gambar yang selalu tampil di halaman publik adalah logo,
> banner & wallpaper gereja yang memang dipasang admin untuk umum — bukan
> foto keluarga.)

## Login Jemaat (halaman publik)

Di bawah kartu pencarian ada kartu **Login Jemaat**. Alurnya:

1. Admin membuka halaman keluarga yang bersangkutan dan mengisi kolom
   **Password Login Jemaat** (sekali saja; kosongkan pada perubahan
   berikutnya bila tidak ingin mengganti). Di daftar keluarga dashboard,
   keluarga seperti ini berlabel **“Login jemaat aktif”**.
2. Jemaat masuk dengan **NIK kepala keluarga** + password itu.
3. Jemaat tiba di **Beranda Jemaat**: kartu keluarga lengkapnya sendiri,
   bagian **Dokumen Saya** (semua dokumen keluarganya, termasuk dokumen
   tiap anak) dengan tombol **Download**, formulir **Ganti Password**, dan
   tombol **Keluar**.

Sesi jemaat dan sesi admin adalah **dua hal yang terpisah**: sesi jemaat
tidak pernah bisa membuka halaman admin. Bila password belum diatur
admin, login jemaat menjawab dengan pesan bahwa login untuk keluarga itu
belum diaktifkan.

## Dokumen keluarga

Di halaman admin tiap keluarga ada bagian **Dokumen Keluarga** dengan
tiga slot berkas: **Akta Nikah**, **Surat Baptis Kepala Keluarga**, dan
**Surat Baptis Pasangan**. Tiap baris anak punya dua slot sendiri:
**Akta Kelahiran** dan **Surat Baptis Anak**. Admin bisa mengunggah,
mengganti (unggah ulang pada slot yang sama), mengunduh, dan menghapus
tiap berkas. Format yang diterima: PDF/JPG/JPEG/PNG/WebP, maks ±10 MB.

Dokumen tersimpan di folder unggahan dengan nama token berpawalan
`dok-…` dan **tidak pernah** disajikan lewat route foto publik —
satu-satunya jalan mengunduhnya adalah `GET /dokumen/<id>` setelah
login: admin boleh mengunduh dokumen apa pun, jemaat **hanya** dokumen
keluarganya sendiri (dokumen keluarga lain menjawab 404). Menghapus
keluarga/anak ikut menghapus berkas dokumennya. Dokumen ikut tercadangkan
dalam **Backup & Pulihkan**.

## Fitur

- **Publik (tanpa login)**: pencarian NIK → kartu keluarga lengkap;
  pencarian nama → daftar minimal; pesan ramah bila data tidak ditemukan;
  kartu **Login Jemaat** di bawah kartu pencarian.
- **Jemaat (login anggota, diaktifkan admin per keluarga)**: Beranda
  Jemaat berisi kartu keluarga sendiri + **Dokumen Saya** yang bisa
  diunduh; bisa ganti password sendiri dan keluar.
- **Admin (satu akun, wajib login)**:
  - CRUD keluarga: nama + NIK kepala keluarga (unik, tepat 16 digit),
    nama + NIK pasangan (opsional), status perkawinan (`Menikah`,
    `Belum Menikah`, `Cerai Hidup`, `Cerai Mati`), tanggal menikah,
    unggah/ganti/hapus **foto pernikahan**, dan kolom **Password Login
    Jemaat** untuk mengaktifkan/mengganti akses masuk keluarga itu.
  - Kelola **anak** per keluarga: tambah/ubah/hapus, **atur urutan**
    (tombol Naik/Turun menentukan label Anak 1, 2, 3, ...), unggah foto
    tiap anak.
  - **Dokumen**: akta nikah + surat baptis kepala/pasangan per keluarga;
    akta kelahiran + surat baptis per anak (unggah/ganti/unduh/hapus).
  - Dashboard dengan pencarian keluarga (nama/NIK), ringkasan jumlah,
    dan lencana per keluarga: **Dokumen: N** dan **Login jemaat aktif**.
  - **Pengaturan**: nama gereja (tampil di kepala halaman & judul halaman
    publik), **logo gereja** (tampil di halaman publik, halaman masuk,
    dan kepala aplikasi; unggah/ganti/hapus), **banner** halaman publik,
    **wallpaper/background** (berlaku di semua halaman;
    unggah/ganti/hapus; bila kosong, halaman memakai latar bawaan), dan
    ganti password admin.
  - **Backup & Pulihkan**: satu ZIP berisi database + seluruh folder
    unggahan (foto, dokumen, banner, wallpaper, logo).
- **Foto**: format JPG/JPEG/PNG/WebP, maks ±5 MB per berkas. **Dokumen**:
  PDF/JPG/JPEG/PNG/WebP, maks ±10 MB. Semua berkas diunggah oleh admin
  sendiri.
- Password (admin maupun jemaat) tersimpan sebagai **hash**
  (PBKDF2-HMAC-SHA256 + salt), session login lewat cookie HttpOnly.
- UI Bahasa Indonesia, ramah HP: sidebar + penanda menu aktif di layar
  lebar; bilah atas gradasi ungu + navigasi bawah di HP.

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
> awal belum diganti. Akun admin hanya satu; selain itu tiap keluarga
> bisa diberi **password login jemaat** sendiri oleh admin (lihat bagian
> Login Jemaat) — akun jemaat bukan akun admin dan tidak bisa membuka
> halaman admin.

Alur pemakaian singkat:

1. Login sebagai admin → menu **Pengaturan**: isi nama gereja, dan bila
   mau, unggah logo, banner & wallpaper.
2. Menu **Tambah Keluarga**: isi data kepala keluarga + pasangan, status,
   tanggal menikah, foto pernikahan, dan (bila keluarga itu boleh masuk
   sendiri) password login jemaatnya.
3. Di halaman keluarga itu, tambahkan anak-anaknya (nama + foto), atur
   urutannya dengan tombol Naik/Turun, lalu unggah dokumennya: akta
   nikah & surat baptis di bagian **Dokumen Keluarga**, akta kelahiran &
   surat baptis di tiap baris anak.
4. Jemaat membuka halaman publik: memasukkan NIK untuk melihat kartu
   keluarganya, atau masuk lewat **Login Jemaat** untuk mengunduh
   dokumen keluarganya.

## Struktur data (SQLite, `data/jemaat.db`)

| Tabel | Isi |
|---|---|
| `users` | akun admin: `username` (unik), `password_hash`, `salt`, `nama` |
| `settings` | pengaturan kunci-nilai: `nama_gereja`, `banner_foto`, `wallpaper_foto`, `logo` (tiga terakhir berisi nama berkas foto atau kosong) |
| `families` | keluarga: `kepala_nama`, `kepala_nik` (unik, 16 digit), `pasangan_nama`, `pasangan_nik` (boleh kosong), `status`, `tanggal_menikah`, `foto_nikah` (nama berkas atau kosong), `kata_sandi_hash`/`kata_sandi_salt` (password login jemaat; NULL = login jemaat nonaktif) |
| `children` | anak: `family_id`, `nama`, `foto` (nama berkas atau kosong), `urutan` |
| `dokumen` | dokumen resmi: `family_id`, `child_id` (NULL = dokumen keluarga), `jenis` (`akta_nikah`, `baptis_kepala`, `baptis_pasangan`, `akta_lahir`, `baptis_anak`), `stored_name` (nama berkas token `dok-…`, unik), `original_name`, `dibuat` |

Session login tersimpan di memori server (bukan di tabel), jadi me-restart
server akan mengeluarkan (logout) admin. Itu disengaja untuk versi
sederhana ini.

## Keamanan foto & dokumen

- Foto disimpan di `data/uploads/` (atau `JEMAAT_UPLOADS`) dengan **nama
  berkas token acak** (24 karakter hex + ekstensi), bukan nama asli
  unggahan — jadi alamat foto tidak bisa ditebak dari nama orang.
- Berkas foto **hanya** disajikan lewat route aplikasi `GET /foto/<nama>`,
  yang menerapkan **daftar putih**: nama harus berpola token DAN tercatat
  di database sebagai foto sungguhan (foto nikah, foto anak, banner,
  wallpaper, atau logo). Tidak ada directory listing, dan percobaan path
  traversal ditolak (404).
- **Dokumen** disimpan dengan nama token berpawalan `dok-…`, yang selalu
  **ditolak** route `/foto/`. Dokumen hanya keluar lewat
  `GET /dokumen/<id>` setelah login — admin untuk semua dokumen, jemaat
  hanya untuk keluarganya sendiri — dan selalu sebagai unduhan lampiran
  dengan nama berkas aslinya.
- Mengganti/menghapus foto atau dokumen, atau menghapus keluarga/anak,
  akan menghapus berkas lamanya dari disk.

## Menguji

`test_alur.py` menjalankan sendiri server uji pada port sementara dengan
database & folder unggahan sementara, lalu memeriksa seluruh alur:

```bash
cd ~/workspace/data-jemaat-gereja
python3 test_alur.py        # akhiri dengan "SEMUA UJI LULUS"
```

Yang dibuktikan: halaman publik & login tampil (200), termasuk kartu
**Login Jemaat** di halaman publik; kerangka aplikasi baru (sidebar +
navigasi bawah + variabel palet) tampil di admin & jemaat; halaman admin
tanpa login diarahkan ke login; login admin; buat keluarga + unggah foto
nikah dan ambil kembali berkasnya; NIK duplikat ditolak; tambah dua anak
(satu berfoto, fotonya terambil utuh); pencarian NIK (kepala & pasangan)
→ kartu lengkap berisi nama anak & foto; pencarian nama → hanya info
minimal (tes menegaskan **tidak ada** URL foto, nama anak, NIK, maupun
tanggal menikah pada responsnya); NIK tak dikenal → pesan ramah;
pengaturan nama gereja tampil di halaman publik; unggah **banner**,
**wallpaper**, dan **logo** → tersaji lewat `/foto/` (daftar putih) dan
dirujuk halaman publik, dan menghapusnya mengembalikan tampilan bawaan;
**dokumen** akta nikah (PDF) & akta kelahiran anak (JPG): unggah, ganti
(berkas lama terhapus), hapus, dan unduh terlindungi — publik ditolak,
nama berkas dokumen ditolak route `/foto/`; **login jemaat**: pesan
“belum diaktifkan” sebelum password diatur, login salah/benar, jemaat
mengunduh dokumennya sendiri tetapi 404 untuk dokumen keluarga lain,
sesi jemaat ditolak area admin, ganti password & keluar berfungsi;
**backup & pulihkan** (termasuk dokumen di dalamnya) mengembalikan
seluruh data di server kosong.

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
