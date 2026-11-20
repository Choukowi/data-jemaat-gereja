#!/usr/bin/env python3
"""Uji alur end-to-end aplikasi Data Jemaat Gereja lewat HTTP.

Skrip ini MENJALANKAN SENDIRI server aplikasi pada port uji dengan
database & folder unggahan SEMENTARA (di /tmp), lalu memeriksanya:

  python3 test_alur.py

Yang dibuktikan:
- GET / dan GET /login -> 200
- Halaman publik SANGAT minimal: judul persis "Cari Keluarga (Nama/NIK)",
  tepat SATU formulir dengan SATU input teks "q" dan tombol "Cari",
  TANPA nav "Masuk Admin", TANPA field tersembunyi cari=nik/cari=nama,
  TANPA teks penjelasan lama, TANPA footer; nama gereja hanya di <title>
- Halaman admin tanpa login diarahkan ke /login
- Login admin berhasil
- Buat keluarga (kepala + pasangan + status + tanggal menikah) + unggah foto nikah
- NIK duplikat ditolak
- Tambah dua anak, salah satunya dengan foto; foto bisa diambil kembali
- Pencarian terpadu: q = NIK kepala -> kartu LENGKAP (nama anak & foto)
- Pencarian terpadu: q = NIK pasangan -> kartu lengkap juga
- Pencarian terpadu: q = potongan NAMA -> hanya info minimal:
  TANPA URL foto, TANPA nama anak, TANPA NIK, TANPA tanggal menikah
- q kosong -> halaman polos tanpa pesan error
- NIK tidak ditemukan -> pesan ramah
- Admin mengunggah BANNER & WALLPAPER di Pengaturan -> keduanya tersaji
  lewat /foto/ dan dirujuk oleh halaman publik; menghapus banner
  mengembalikan halaman publik ke tampilan bawaan
- Respons HASIL pencarian (nama & NIK) memuat skrip history.replaceState
  (menghapus ?q=... dari address bar, jadi REFRESH membuka halaman awal
  yang polos) + tautan "↺ Cari Lagi" (href="/"); halaman polos tanpa q
  TIDAK memuat keduanya
- Backup & Pulihkan (khusus admin): unduh ZIP berisi jemaat.db +
  manifest.json (app="data-jemaat-gereja") + foto di uploads/;
  memulihkan ZIP itu di server BARU yang kosong mengembalikan keluarga,
  foto, dan kartu publiknya; berkas non-ZIP dan ZIP milik aplikasi lain
  ditolak tanpa mengubah data
"""
import io, json, os, re, subprocess, sys, tempfile, time, urllib.request, urllib.parse
import urllib.error, http.cookiejar, uuid, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("TEST_PORT", "8791"))
BASE = f"http://127.0.0.1:{PORT}"

# PNG 1x1 piksel (berkas gambar uji terkecil)
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000000020001e221bc330000000049454e44ae426082")

NIK_KEPALA = "3201010101800001"
NIK_PASANGAN = "3201014101800002"
TGL_ISO = "2010-05-12"
TGL_ID = "12 Mei 2010"


def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def req(op, path, data=None):
    url = BASE + path
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = op.open(urllib.request.Request(url, data=body))
    return r.status, r.geturl(), r.read().decode("utf-8", "replace")


def req_multipart(op, path, fields, files):
    """files: nama_field -> (nama_berkas, bytes, content_type)"""
    boundary = "----uji" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode() + b"\r\n")
    for name, (fname, data, ctype) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append((f'Content-Disposition: form-data; name="{name}"; '
                       f'filename="{fname}"\r\n').encode())
        chunks.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        chunks.append(data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    r = op.open(urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    return r.status, r.geturl(), r.read().decode("utf-8", "replace")


def req_bytes(op, path):
    """GET yang mengembalikan body sebagai bytes (untuk unduhan ZIP)."""
    r = op.open(BASE + path)
    return r.status, r.geturl(), r.headers, r.read()


def must(cond, msg):
    print(("OK  " if cond else "GAGAL ") + msg)
    if not cond:
        sys.exit(1)


def main():
    tmp = tempfile.mkdtemp(prefix="jemaat-test-")
    env = dict(os.environ)
    env["JEMAAT_DB"] = os.path.join(tmp, "sub", "jemaat.db")
    env["JEMAAT_UPLOADS"] = os.path.join(tmp, "uploads")
    env["PORT"] = str(PORT)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # tunggu server siap
        siap = False
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/login", timeout=2)
                siap = True
                break
            except Exception:
                time.sleep(0.2)
        must(siap, "server uji menyala")

        publik = client()
        # 1. Halaman publik & login tampil; halaman publik sangat minimal
        s, url, home = req(publik, "/")
        must(s == 200, "GET / -> 200")
        must("<h2>Cari Keluarga (Nama/NIK)</h2>" in home,
             "halaman publik: judul persis 'Cari Keluarga (Nama/NIK)'")
        must(home.count("<form") == 1 and home.count('name="q"') == 1
             and ">Cari</button>" in home,
             "halaman publik: tepat SATU formulir, SATU input q, tombol 'Cari'")
        must("Masuk Admin" not in home and "<nav>" not in home,
             "halaman publik: TANPA nav / tautan Masuk Admin")
        must('name="cari"' not in home and 'name="nik"' not in home
             and 'name="nama"' not in home,
             "halaman publik: TANPA field tersembunyi cari=nik/cari=nama")
        must("Selamat datang di direktori keluarga" not in home
             and "Cari dengan NIK (detail lengkap)" not in home
             and "Cari dengan Nama (daftar singkat)" not in home,
             "halaman publik: TANPA teks penjelasan lama")
        must("<footer>" not in home and "Direktori Keluarga Jemaat" not in home,
             "halaman publik: TANPA footer")
        s, url, login_page = req(publik, "/login")
        must(s == 200 and "Masuk Admin" in login_page, "GET /login -> 200")

        # 2. Admin tanpa login -> diarahkan ke login
        s, url, _ = req(publik, "/admin")
        must("/login" in url, f"GET /admin tanpa login diarahkan ke login -> {url}")

        # 3. Login admin
        admin = client()
        s, url, _ = req(admin, "/login", {"username": "admin", "password": "admin123"})
        must("/admin" in url, f"login admin berhasil -> {url}")

        # 4. Buat keluarga + unggah foto nikah
        fields = {"kepala_nama": "Budi Uji Santoso", "kepala_nik": NIK_KEPALA,
                  "pasangan_nama": "Sari Uji Rahayu", "pasangan_nik": NIK_PASANGAN,
                  "status": "Menikah", "tanggal_menikah": TGL_ISO}
        s, url, page = req_multipart(
            admin, "/admin/keluarga/baru", fields,
            {"foto_nikah": ("nikah.png", PNG_1PX, "image/png")})
        m = re.search(r"/admin/keluarga/(\d+)", url)
        must(m is not None, f"keluarga dibuat -> {url}")
        fid = m.group(1)
        s, url, form_page = req(admin, f"/admin/keluarga/{fid}")
        must("Budi Uji Santoso" in form_page and "/foto/" in form_page,
             "halaman ubah keluarga menampilkan foto nikah yang diunggah")
        m_foto = re.search(r"/foto/([a-f0-9]+\.png)", form_page)
        must(m_foto is not None, "nama berkas foto nikah ditemukan di halaman admin")
        r = admin.open(BASE + "/foto/" + m_foto.group(1))
        data_foto = r.read()
        must(r.status == 200 and r.headers.get_content_type() == "image/png"
             and data_foto == PNG_1PX, "foto nikah bisa diambil kembali lewat /foto/ (utuh)")

        # 5. NIK duplikat ditolak
        s, url, page = req_multipart(admin, "/admin/keluarga/baru", fields, {})
        must("sudah terdaftar" in page, "membuat keluarga dengan NIK yang sama ditolak")

        # 6. Tambah dua anak (anak pertama dengan foto)
        req_multipart(admin, f"/admin/keluarga/{fid}/anak",
                      {"nama": "Anak Uji Satu"},
                      {"foto": ("anak1.png", PNG_1PX, "image/png")})
        req_multipart(admin, f"/admin/keluarga/{fid}/anak",
                      {"nama": "Anak Uji Dua"}, {})
        s, url, form_page = req(admin, f"/admin/keluarga/{fid}")
        must("Anak Uji Satu" in form_page and "Anak Uji Dua" in form_page,
             "dua anak tampil di halaman admin keluarga")

        # 7. Pencarian terpadu: q = NIK kepala (16 digit) -> kartu lengkap
        q = urllib.parse.urlencode({"q": NIK_KEPALA})
        s, url, kartu = req(publik, "/?" + q)
        must("Budi Uji Santoso" in kartu and "Sari Uji Rahayu" in kartu
             and "Menikah" in kartu and TGL_ID in kartu,
             "q=NIK kepala: kartu lengkap berisi nama, status, tanggal menikah terbaca")
        must("Anak Uji Satu" in kartu and "Anak Uji Dua" in kartu
             and "Anak 1" in kartu and "Anak 2" in kartu,
             "q=NIK kepala: daftar anak tampil dengan label Anak 1 & Anak 2")
        must("/foto/" in kartu, "q=NIK kepala: foto (nikah/anak) tampil di kartu lengkap")

        # 8. Pencarian terpadu: q = NIK pasangan -> kartu lengkap juga
        q = urllib.parse.urlencode({"q": NIK_PASANGAN})
        s, url, kartu2 = req(publik, "/?" + q)
        must("Anak Uji Satu" in kartu2 and "/foto/" in kartu2,
             "q=NIK pasangan: kartu lengkap tampil juga")

        # 9. Pencarian terpadu: q = potongan NAMA -> HANYA info minimal
        q = urllib.parse.urlencode({"q": "Budi Uji"})
        s, url, daftar = req(publik, "/?" + q)
        must("Budi Uji Santoso" in daftar and "Sari Uji Rahayu" in daftar
             and "Menikah" in daftar,
             "q=nama: daftar minimal berisi nama kepala, pasangan, status")
        must("detail lengkap" in daftar and "NIK" in daftar,
             "q=nama: ada satu baris petunjuk untuk mencari dengan NIK")
        must("/foto/" not in daftar, "q=nama: TIDAK ada URL foto sama sekali")
        must("Anak Uji Satu" not in daftar and "Anak Uji Dua" not in daftar,
             "q=nama: TIDAK ada nama anak")
        must(NIK_KEPALA not in daftar and NIK_PASANGAN not in daftar,
             "q=nama: TIDAK ada NIK yang bocor")
        must(TGL_ISO not in daftar and TGL_ID not in daftar,
             "q=nama: TIDAK ada tanggal menikah")

        # 10. Tidak ditemukan -> pesan ramah; q kosong -> halaman polos
        q = urllib.parse.urlencode({"q": "9999999999999999"})
        s, url, kosong = req(publik, "/?" + q)
        must("tidak ditemukan" in kosong, "q=NIK tak dikenal -> pesan ramah 'tidak ditemukan'")
        q = urllib.parse.urlencode({"q": "Zzz Namaacak"})
        s, url, kosong2 = req(publik, "/?" + q)
        must("Tidak ada keluarga dengan nama itu" in kosong2,
             "q=nama tak dikenal -> pesan ramah tidak ditemukan")
        s, url, polos = req(publik, "/?q=")
        must("<h2>Cari Keluarga (Nama/NIK)</h2>" in polos
             and "tidak ditemukan" not in polos and "flash warn" not in polos,
             "q kosong -> halaman polos tanpa pesan error")

        # 11. Pengaturan nama gereja -> di halaman publik HANYA di <title>
        req(admin, "/admin/pengaturan", {"nama_gereja": "GKI Uji Sejahtera"})
        s, url, home2 = req(publik, "/")
        m_title = re.search(r"<title>(.*?)</title>", home2)
        must(m_title is not None and "GKI Uji Sejahtera" in m_title.group(1),
             "nama gereja dari Pengaturan tampil di <title> halaman publik")
        must("<h1>" not in home2 and "<header>" not in home2,
             "halaman publik: TANPA header berisi nama gereja")

        # 12. Banner & wallpaper halaman publik (diunggah admin di Pengaturan)
        s, url, home_tanpa = req(publik, "/")
        must('class="banner"' not in home_tanpa and "background-image" not in home_tanpa,
             "sebelum diatur: halaman publik memakai tampilan bawaan (tanpa banner/wallpaper)")
        req_multipart(admin, "/admin/pengaturan/banner", {},
                      {"gambar": ("banner.png", PNG_1PX, "image/png")})
        req_multipart(admin, "/admin/pengaturan/wallpaper", {},
                      {"gambar": ("wallpaper.png", PNG_1PX, "image/png")})
        s, url, home3 = req(publik, "/")
        m_banner = re.search(r'class="banner" src="/foto/([a-f0-9]+\.png)"', home3)
        must(m_banner is not None, "banner tampil di bagian atas halaman publik")
        r = publik.open(BASE + "/foto/" + m_banner.group(1))
        must(r.status == 200 and r.read() == PNG_1PX,
             "berkas banner tersaji utuh lewat /foto/")
        m_wp = re.search(r"background-image:url\('/foto/([a-f0-9]+\.png)'\)", home3)
        must(m_wp is not None, "wallpaper menjadi background halaman publik")
        r = publik.open(BASE + "/foto/" + m_wp.group(1))
        must(r.status == 200 and r.read() == PNG_1PX,
             "berkas wallpaper tersaji utuh lewat /foto/")
        # menghapus banner -> halaman publik kembali tanpa banner (wallpaper tetap)
        req(admin, "/admin/pengaturan/banner-hapus", {})
        s, url, home4 = req(publik, "/")
        must('class="banner"' not in home4 and "background-image" in home4,
             "setelah banner dihapus: banner hilang, wallpaper tetap tampil")

        # 13. Refresh setelah mencari -> kembali ke halaman awal polos:
        # respons hasil pencarian memuat skrip history.replaceState yang
        # menghapus ?q=... dari address bar + tautan "Cari Lagi" ke "/";
        # halaman polos tanpa q TIDAK memuat keduanya.
        q = urllib.parse.urlencode({"q": "Budi Uji"})
        s, url, daftar2 = req(publik, "/?" + q)
        must("history.replaceState" in daftar2,
             "hasil cari NAMA: memuat skrip history.replaceState")
        must('<a href="/">↺ Cari Lagi</a>' in daftar2,
             "hasil cari NAMA: ada tautan '↺ Cari Lagi' dengan href=\"/\"")
        q = urllib.parse.urlencode({"q": NIK_KEPALA})
        s, url, kartu3 = req(publik, "/?" + q)
        must("history.replaceState" in kartu3,
             "hasil cari NIK: memuat skrip history.replaceState")
        must('<a href="/">↺ Cari Lagi</a>' in kartu3,
             "hasil cari NIK: ada tautan '↺ Cari Lagi' dengan href=\"/\"")
        q = urllib.parse.urlencode({"q": "9999999999999999"})
        s, url, kosong3 = req(publik, "/?" + q)
        must("history.replaceState" in kosong3
             and '<a href="/">↺ Cari Lagi</a>' in kosong3,
             "hasil TIDAK ditemukan: ada juga skrip replaceState + tautan '↺ Cari Lagi'")
        s, url, polos2 = req(publik, "/")
        must("history.replaceState" not in polos2,
             "halaman polos tanpa q: TIDAK ada skrip history.replaceState")
        must("Cari Lagi" not in polos2,
             "halaman polos tanpa q: TIDAK ada tautan 'Cari Lagi'")

        # 14. Backup & Pulihkan: halaman khusus admin, unduh ZIP lengkap,
        # dan penolakan berkas yang salah (data tidak berubah).
        s, url, _ = req(publik, "/admin/backup")
        must("/login" in url, f"GET /admin/backup tanpa login diarahkan ke login -> {url}")
        s, url, hal_backup = req(admin, "/admin/backup")
        must(s == 200 and "/admin/backup/unduh" in hal_backup
             and 'name="berkas"' in hal_backup and "MENGGANTI" in hal_backup,
             "halaman Backup & Pulihkan: tautan unduh + formulir pulihkan + peringatan tampil")
        s, url, hdr, zip_bytes = req_bytes(admin, "/admin/backup/unduh")
        must(s == 200 and hdr.get_content_type() == "application/zip",
             "GET /admin/backup/unduh -> 200 application/zip")
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        must("jemaat.db" in names and "manifest.json" in names,
             "zip backup berisi jemaat.db + manifest.json")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        must(manifest.get("app") == "data-jemaat-gereja" and manifest.get("versi") == 1
             and "dibuat" in manifest,
             "manifest backup: app='data-jemaat-gereja', versi=1, ada cap waktu")
        foto_zip = "uploads/" + m_foto.group(1)
        must(foto_zip in names and zf.read(foto_zip) == PNG_1PX,
             "zip backup berisi foto keluarga di uploads/ (utuh)")
        # Pulihkan berkas BUKAN zip -> ditolak, data tidak berubah.
        s, url, page = req_multipart(
            admin, "/admin/backup/pulihkan", {},
            {"berkas": ("bukan.zip", b"ini bukan zip sama sekali", "application/zip")})
        must("tidak valid" in page, "pulihkan berkas non-ZIP ditolak")
        s, url, dash = req(admin, "/admin")
        must("Budi Uji Santoso" in dash,
             "setelah pulihkan non-ZIP gagal: data keluarga tetap ada")
        # Pulihkan zip milik APLIKASI LAIN -> ditolak dengan pesan khusus.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json", json.dumps({"app": "aplikasi-lain", "versi": 1}))
            z.writestr("jemaat.db", b"database palsu")
        s, url, page = req_multipart(
            admin, "/admin/backup/pulihkan", {},
            {"berkas": ("backup-lain.zip", buf.getvalue(), "application/zip")})
        must("Berkas ini bukan backup aplikasi data jemaat." in page,
             "pulihkan zip aplikasi lain ditolak dengan pesan khusus")
        s, url, dash = req(admin, "/admin")
        must("Budi Uji Santoso" in dash,
             "setelah pulihkan zip aplikasi lain gagal: data keluarga tetap ada")

        # 15. Pulihkan backup yang sama di server BARU yang benar-benar
        # kosong -> keluarga, foto, dan kartu publiknya kembali semua.
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except Exception:
            proc.kill()
            proc.communicate()
        tmp2 = tempfile.mkdtemp(prefix="jemaat-test-pulih-")
        env2 = dict(os.environ)
        env2["JEMAAT_DB"] = os.path.join(tmp2, "sub", "jemaat.db")
        env2["JEMAAT_UPLOADS"] = os.path.join(tmp2, "uploads")
        env2["PORT"] = str(PORT)
        proc2 = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "app.py")],
            env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            siap2 = False
            for _ in range(50):
                try:
                    urllib.request.urlopen(BASE + "/login", timeout=2)
                    siap2 = True
                    break
                except Exception:
                    time.sleep(0.2)
            must(siap2, "server uji kedua (masih kosong) menyala")
            admin2 = client()
            s, url, _ = req(admin2, "/login", {"username": "admin", "password": "admin123"})
            must("/admin" in url, "login admin di server kosong berhasil")
            s, url, dash_kosong = req(admin2, "/admin")
            must("Budi Uji Santoso" not in dash_kosong,
                 "server kosong: keluarga memang belum ada")
            s, url, page = req_multipart(
                admin2, "/admin/backup/pulihkan", {},
                {"berkas": ("backup-jemaat.zip", zip_bytes, "application/zip")})
            must("/login" in url and "Backup dipulihkan. Silakan masuk lagi." in page,
                 f"pulihkan backup -> diarahkan ke login + pesan sukses -> {url}")
            s, url, _ = req(admin2, "/login", {"username": "admin", "password": "admin123"})
            must("/admin" in url, "login lagi setelah pulihkan berhasil")
            s, url, dash2 = req(admin2, "/admin")
            must("Budi Uji Santoso" in dash2,
                 "setelah pulihkan: keluarga kembali di daftar admin")
            r = admin2.open(BASE + "/foto/" + m_foto.group(1))
            must(r.status == 200 and r.read() == PNG_1PX,
                 "setelah pulihkan: foto keluarga tersaji lagi lewat /foto/ (utuh)")
            q = urllib.parse.urlencode({"q": NIK_KEPALA})
            s, url, kartu_pulih = req(publik, "/?" + q)
            must("Budi Uji Santoso" in kartu_pulih and "Anak Uji Satu" in kartu_pulih,
                 "setelah pulihkan: pencarian NIK publik menemukan kartu keluarga lagi")
            must(os.path.isfile(os.path.join(tmp2, "sub", "jemaat-sebelum-pulih.db")),
                 "salinan pengaman jemaat-sebelum-pulih.db tersimpan di samping DB aktif")
        finally:
            proc2.terminate()
            try:
                proc2.communicate(timeout=5)
            except Exception:
                proc2.kill()
                proc2.communicate()

        print("\nSEMUA UJI LULUS")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except Exception:
            proc.kill()
            out, _ = proc.communicate()
        if os.environ.get("TEST_VERBOSE"):
            print(out)


if __name__ == "__main__":
    main()
