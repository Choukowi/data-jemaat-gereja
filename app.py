#!/usr/bin/env python3
"""
Data Jemaat Gereja — Direktori Keluarga Jemaat
Stack: Python 3 standard library SAJA (http.server + sqlite3).
Tanpa framework web dan tanpa library eksternal sama sekali.

Dua sisi aplikasi:
1. PUBLIK (tanpa login): halaman sangat minimal — banner (bila diatur),
   judul "Cari Keluarga (Nama/NIK)", dan SATU formulir pencarian (input q).
   - Bila q tepat 16 digit angka, q diperlakukan sebagai NIK (milik kepala
     keluarga ATAU pasangan) -> tampil KARTU KELUARGA LENGKAP: foto nikah,
        status perkawinan, tanggal menikah, nama kepala keluarga, nama
        pasangan, dan daftar anak (Anak 1, Anak 2, ...) beserta foto tiap
        anak bila ada.
   - Selain itu q diperlakukan sebagai NAMA (sebagian nama) -> tampil
        DAFTAR MINIMAL saja: nama kepala keluarga, nama pasangan, status
        perkawinan. TANPA foto, TANPA anak, TANPA tanggal menikah, TANPA NIK.
        Pengaman ini SENGAJA: detail lengkap + foto hanya bisa dibuka
        dengan NIK yang tepat.
2. ADMIN (satu akun admin, wajib login): mengelola data keluarga & anak,
   mengunggah foto dan dokumen resmi (akta nikah, akta kelahiran, surat
   baptis), mengatur nama gereja, logo, banner & wallpaper halaman
   publik, serta mengaktifkan login jemaat per keluarga.
3. JEMAAT (login anggota, diaktifkan admin per keluarga): masuk dari
   formulir "Login Jemaat" di halaman publik dengan NIK kepala keluarga
   + password. Jemaat melihat kartu keluarganya sendiri dan dapat
   mengunduh dokumen keluarganya (lewat /dokumen/<id> yang terlindungi;
   dokumen TIDAK pernah tersaji lewat route foto publik).

Jalankan:  python3 app.py            (default http://127.0.0.1:8000)
           PORT=8080 python3 app.py
           JEMAAT_DB=/path/jemaat.db JEMAAT_UPLOADS=/path/uploads python3 app.py
"""
import datetime
import hashlib
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import urllib.parse
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("JEMAAT_DB", os.path.join(BASE_DIR, "data", "jemaat.db"))
UPLOADS_DIR = os.environ.get("JEMAAT_UPLOADS", os.path.join(BASE_DIR, "data", "uploads"))
PORT = int(os.environ.get("PORT", "8000"))

STATUS_PERKAWINAN = ("Menikah", "Belum Menikah", "Cerai Hidup", "Cerai Mati")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FOTO_BYTES = 5 * 1024 * 1024          # ~5 MB per foto
MAX_BODY_BYTES = 12 * 1024 * 1024         # batas total satu request multipart
MAX_BACKUP_BYTES = 200 * 1024 * 1024      # berkas backup boleh besar (banyak foto)
FOTO_NAME_RE = re.compile(r"[a-f0-9]{24}\.(?:jpg|jpeg|png|webp)")

# Dokumen keluarga (akta nikah, akta kelahiran, surat baptis). Berkasnya
# disimpan di folder uploads yang sama dengan foto, tetapi nama simpannya
# berpawalan "dok-" sehingga route publik /foto/ TIDAK pernah menyajikannya
# (dokumen hanya bisa diunduh lewat /dokumen/<id> setelah login).
DOK_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MAX_DOK_BYTES = 10 * 1024 * 1024          # ~10 MB per berkas dokumen
MAX_DOK_BODY_BYTES = 16 * 1024 * 1024     # batas request unggah dokumen
DOK_NAME_RE = re.compile(r"dok-[a-f0-9]{24}\.(?:pdf|jpg|jpeg|png|webp)")
DOK_KELUARGA = {
    "akta_nikah": "Akta Nikah",
    "baptis_kepala": "Surat Baptis Kepala Keluarga",
    "baptis_pasangan": "Surat Baptis Pasangan",
}
DOK_ANAK = {
    "akta_lahir": "Akta Kelahiran",
    "baptis_anak": "Surat Baptis Anak",
}
DOK_LABEL = {**DOK_KELUARGA, **DOK_ANAK}
BULAN_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
            "Agustus", "September", "Oktober", "November", "Desember"]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    nama TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kepala_nama TEXT NOT NULL,
    kepala_nik TEXT UNIQUE NOT NULL,        -- NIK kepala keluarga, tepat 16 digit
    pasangan_nama TEXT NOT NULL DEFAULT '',
    pasangan_nik TEXT,                      -- NIK pasangan, boleh kosong; bila diisi 16 digit
    status TEXT NOT NULL DEFAULT 'Menikah'
        CHECK (status IN ('Menikah','Belum Menikah','Cerai Hidup','Cerai Mati')),
    tanggal_menikah TEXT,                   -- YYYY-MM-DD, boleh kosong
    foto_nikah TEXT,                        -- nama berkas foto di folder uploads, boleh kosong
    kata_sandi_hash TEXT,                   -- password LOGIN JEMAAT; NULL = login jemaat nonaktif
    kata_sandi_salt TEXT
);
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    foto TEXT,                              -- nama berkas foto anak, boleh kosong
    urutan INTEGER NOT NULL DEFAULT 0       -- urutan tampil (Anak 1, 2, 3, ...)
);
CREATE TABLE IF NOT EXISTS dokumen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
    jenis TEXT NOT NULL,                    -- akta_nikah/baptis_kepala/baptis_pasangan (keluarga)
                                            -- atau akta_lahir/baptis_anak (per anak)
    stored_name TEXT UNIQUE NOT NULL,       -- nama berkas token "dok-..." di folder uploads
    original_name TEXT NOT NULL,            -- nama berkas asli dari pengunggah
    dibuat TEXT NOT NULL DEFAULT ''
);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 200_000)
    return dk.hex(), salt


def verify_password(password, password_hash, salt):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 200_000)
    return secrets.compare_digest(dk.hex(), password_hash)


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def nama_gereja(conn):
    return get_setting(conn, "nama_gereja", "Gereja Kami") or "Gereja Kami"


# ---------------------------------------------------------------------------
# Warna tema (bisa diganti admin di Pengaturan; bawaan = krem)
# Satu warna dasar (#rrggbb, kunci settings 'warna_tema') menggerakkan
# variabel CSS: --warna-utama = warna itu sendiri, --warna-utama-gelap =
# bayangan lebih gelap (RGB x 0,62), --warna-utama-muda = versi sangat
# terang, dan warna teks di atas bidang berwarna dipilih dari luminansi
# (cokelat tua di atas warna terang, putih di atas warna gelap) supaya
# tombol/lencana/menu selalu terbaca — termasuk tema krem yang terang.
# ---------------------------------------------------------------------------
WARNA_TEMA_DEFAULT = "#efe3c8"
WARNA_TEMA_PRESET = (
    ("Krem", "#efe3c8"),
    ("Ungu", "#6d28d9"),
    ("Biru", "#2563eb"),
    ("Hijau", "#15803d"),
    ("Marun", "#9f1239"),
    ("Cokelat", "#92400e"),
)
WARNA_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
TEKS_DI_TERANG = "#43302b"  # cokelat tua untuk bidang berwarna terang


def _rgb(hexs):
    return tuple(int(hexs[i:i + 2], 16) for i in (1, 3, 5))


def _ke_hex(r, g, b):
    return "#%02x%02x%02x" % (r, g, b)


def warna_gelap(hexs, faktor=0.62):
    """Bayangan lebih gelap dari warna tema: tiap kanal RGB x faktor."""
    r, g, b = _rgb(hexs)
    return _ke_hex(round(r * faktor), round(g * faktor), round(b * faktor))


def warna_muda(hexs, frac=0.85):
    """Versi sangat terang: warna digeser menuju putih sebesar frac."""
    r, g, b = _rgb(hexs)
    return _ke_hex(round(r + (255 - r) * frac),
                   round(g + (255 - g) * frac),
                   round(b + (255 - b) * frac))


def _luminansi(hexs):
    """Luminansi relatif (WCAG) 0..1 dari warna #rrggbb."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hexs)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def teks_di(warna):
    """Warna teks di atas bidang berwarna `warna`: putih atau cokelat tua,
    dipilih yang kontras luminansinya lebih baik."""
    lum = _luminansi(warna)
    kontras_putih = 1.05 / (lum + 0.05)
    kontras_gelap = (lum + 0.05) / (_luminansi(TEKS_DI_TERANG) + 0.05)
    return "#ffffff" if kontras_putih >= kontras_gelap else TEKS_DI_TERANG


def tema_dari(warna):
    """(blok <style> penimpa variabel :root, kelas tema) untuk warna tema
    tersimpan. Warna tak sah -> bawaan krem."""
    if not warna or not WARNA_HEX_RE.fullmatch(warna.strip()):
        warna = WARNA_TEMA_DEFAULT
    warna = warna.strip().lower()
    gelap = warna_gelap(warna)
    style = (f"<style>:root{{--warna-utama:{warna};"
             f"--warna-utama-gelap:{gelap};"
             f"--warna-utama-muda:{warna_muda(warna)};"
             f"--teks-di-utama:{teks_di(warna)};"
             f"--teks-di-gelap:{teks_di(gelap)};}}</style>")
    kelas = "tema-terang" if teks_di(warna) == TEKS_DI_TERANG else "tema-gelap"
    return style, kelas


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    # Migrasi ringan untuk database lama: kolom password login jemaat.
    kolom = {r[1] for r in conn.execute("PRAGMA table_info(families)")}
    if "kata_sandi_hash" not in kolom:
        conn.execute("ALTER TABLE families ADD COLUMN kata_sandi_hash TEXT")
    if "kata_sandi_salt" not in kolom:
        conn.execute("ALTER TABLE families ADD COLUMN kata_sandi_salt TEXT")
    conn.commit()
    row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
    if row["c"] == 0:
        # Akun admin AWAL khusus pengujian lokal. WAJIB diganti sebelum produksi.
        ph, salt = hash_password("admin123")
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, nama) VALUES (?,?,?,?)",
            ("admin", ph, salt, "Administrator (akun awal lokal)"))
        conn.commit()
        print("[init] Akun admin awal dibuat: username=admin (password ada di README). "
              "WAJIB ganti password sebelum dipakai beneran.")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nama_gereja', 'Gereja Kami')")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Session login (server-side, cookie hanya menyimpan token acak)
# Dua tipe sesi yang TERPISAH JELAS:
#   {"tipe": "admin",  "user_id": ...}   -> lolos penjaga admin
#   {"tipe": "jemaat", "family_id": ...} -> hanya untuk halaman jemaat;
#                                           TIDAK PERNAH lolos penjaga admin
# ---------------------------------------------------------------------------
LOGIN_SESSIONS = {}  # token -> dict sesi


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def format_tanggal(iso):
    """'2010-05-12' -> '12 Mei 2010'. String lain dikembalikan apa adanya."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", (iso or "").strip())
    if not m:
        return iso or ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12:
        return f"{d} {BULAN_ID[mo]} {y}"
    return iso


def valid_nik(nik):
    return bool(re.fullmatch(r"\d{16}", nik or ""))


def nik_terpakai(conn, nik, exclude_family_id=None):
    """True bila NIK sudah dipakai keluarga LAIN (sebagai kepala atau pasangan)."""
    q = "SELECT id FROM families WHERE (kepala_nik=? OR pasangan_nik=?)"
    args = [nik, nik]
    if exclude_family_id is not None:
        q += " AND id<>?"
        args.append(exclude_family_id)
    return conn.execute(q, args).fetchone() is not None


# ---------------------------------------------------------------------------
# Unggah foto (multipart, standard library saja)
# ---------------------------------------------------------------------------
def save_upload(files, field):
    """Simpan satu berkas foto dari form multipart.
    Kembalikan (nama_berkas, pesan_error). nama_berkas None bila tidak ada
    berkas yang dipilih (itu bukan error). Nama berkas hasil adalah token
    acak + ekstensi, jadi tidak bisa ditebak/didaftar dari luar."""
    if field not in files:
        return None, ""
    fname, data = files[field]
    if not fname:
        return None, ""
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_EXT:
        return None, "Format foto harus JPG, JPEG, PNG, atau WebP."
    if not data:
        return None, "Berkas foto kosong."
    if len(data) > MAX_FOTO_BYTES:
        return None, "Ukuran foto melebihi 5 MB."
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    new_name = secrets.token_hex(12) + ext
    with open(os.path.join(UPLOADS_DIR, new_name), "wb") as fh:
        fh.write(data)
    return new_name, ""


def delete_upload(name):
    """Hapus berkas unggahan (foto ATAU dokumen) dari folder uploads.
    Hanya nama berpola token yang dikenal yang disentuh."""
    if name and (FOTO_NAME_RE.fullmatch(name) or DOK_NAME_RE.fullmatch(name)):
        try:
            os.remove(os.path.join(UPLOADS_DIR, name))
        except OSError:
            pass


def save_dokumen(files, field):
    """Simpan satu berkas dokumen dari form multipart.
    Kembalikan (nama_simpan, nama_asli, pesan_error). Nama simpan berpola
    "dok-<token acak><ekstensi>" — pola ini ditolak route /foto/, jadi
    dokumen TIDAK bisa diakses publik; hanya lewat /dokumen/<id>."""
    if field not in files:
        return None, "", ""
    fname, data = files[field]
    if not fname:
        return None, "", ""
    ext = os.path.splitext(fname)[1].lower()
    if ext not in DOK_EXT:
        return None, "", "Format dokumen harus PDF, JPG, JPEG, PNG, atau WebP."
    if not data:
        return None, "", "Berkas dokumen kosong."
    if len(data) > MAX_DOK_BYTES:
        return None, "", "Ukuran dokumen melebihi 10 MB."
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    new_name = "dok-" + secrets.token_hex(12) + ext
    with open(os.path.join(UPLOADS_DIR, new_name), "wb") as fh:
        fh.write(data)
    return new_name, os.path.basename(fname), ""


# ---------------------------------------------------------------------------
# HTML layout — kerangka aplikasi modern (sidebar desktop + bilah atas &
# navigasi bawah di HP), palet warna tema yang bisa diganti admin di
# Pengaturan (bawaan krem) + aksen emas. Varian:
#   publik=True  -> halaman publik minimal (tanpa nav/header/footer;
#                   nama gereja hanya di <title>)
#   user=...     -> kerangka ADMIN (sidebar/bottom-nav admin)
#   member=...   -> kerangka JEMAAT (sidebar/bottom-nav jemaat)
#   tanpa keduanya (mis. halaman /login) -> kartu tengah polos bermerek
# ---------------------------------------------------------------------------
def _situs():
    """Pengaturan tampilan situs dari Pengaturan: (wallpaper, logo, warna tema)."""
    conn = db()
    wp = get_setting(conn, "wallpaper_foto", "")
    logo = get_setting(conn, "logo", "")
    warna = get_setting(conn, "warna_tema", "")
    conn.close()
    return wp, logo, warna


def logo_html(logo, css_class="logo-img"):
    """Logo gereja dari Pengaturan; bila belum diatur, tanda bawaan
    (salib dalam lingkaran emas) dengan warna palet."""
    if logo:
        return f'<img class="{css_class}" src="/foto/{esc(logo)}" alt="Logo gereja">'
    return ('<span class="logo-mark" aria-hidden="true">'
            '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="currentColor" '
            'd="M11 2h2v6.5h6.5v2H13V22h-2V10.5H4.5v-2H11z"/></svg></span>')


_IKON = {
    "beranda": '<path d="M3 11l9-7 9 7"/><path d="M5 9.5V21h14V9.5"/><path d="M9.5 21v-6h5v6"/>',
    "tambah": '<path d="M12 5v14M5 12h14"/>',
    "keluarga": ('<circle cx="9" cy="8" r="3.5"/><path d="M3.5 20c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/>'
                 '<circle cx="17" cy="9" r="2.6"/><path d="M15.6 15.2c2.3.3 4.2 1.9 4.8 4.8"/>'),
    "atur": ('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/>'
             '<circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/>'),
    "backup": '<path d="M12 3v11"/><path d="M7 10l5 5 5-5"/><path d="M4 20h16"/>',
    "globe": ('<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>'
              '<path d="M12 3c2.5 2.6 3.8 5.7 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z"/>'),
    "dokumen": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/>',
    "kunci": '<circle cx="8" cy="15" r="4.5"/><path d="M11.5 11.5 20 3"/><path d="M16 7l3 3"/>',
    "keluar": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
}


def ikon(nama):
    return (f'<svg class="ikon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            f'{_IKON.get(nama, "")}</svg>')


def _tautan_nav(item, aktif):
    kunci, href, label, nama_ikon = item
    cls = ' class="aktif"' if kunci == aktif else ""
    return f'<a href="{href}"{cls}>{ikon(nama_ikon)}<span>{esc(label)}</span></a>'


def layout(user, church, title, body, flash="", bg=None, publik=False,
           extra_script="", member=None, aktif=""):
    # bg: nama berkas wallpaper (dari Pengaturan) untuk latar halaman.
    # Bila tidak diberikan, diambil dari Pengaturan — wallpaper berlaku
    # di semua halaman. extra_script: skrip tambahan persis sebelum
    # </body> (dipakai halaman publik hasil pencarian untuk replaceState).
    wallpaper, logo, warna_tema = _situs()
    tema_style, tema_kelas = tema_dari(warna_tema)
    latar = bg if bg is not None else wallpaper
    bg_style = ""
    if latar:
        bg_style = (f"<style>body{{background-image:url('/foto/{esc(latar)}');"
                    "background-size:cover;background-position:center;"
                    "background-attachment:fixed;}</style>")
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    if publik:
        # Halaman publik tetap sangat minimal: tanpa nav, tanpa header,
        # tanpa footer; nama gereja hanya di <title>.
        kerangka_buka = '<main class="publik-main">'
        kerangka_tutup = "</main>"
        return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - {esc(church)}</title>
<link rel="stylesheet" href="/static/style.css">{tema_style}{bg_style}</head>
<body class="publik {tema_kelas}">{kerangka_buka}{flash_html}{body}{kerangka_tutup}
<script src="/static/app.js"></script>{extra_script}
</body></html>"""
    if user or member:
        if user:
            nama_tampil = user["nama"]
            peran = "Admin Gereja"
            awal = (user["nama"] or "A").strip()[:1].upper()
            item_samping = [
                ("dashboard", "/admin", "Dashboard", "beranda"),
                ("tambah", "/admin/keluarga/baru", "Tambah Keluarga", "tambah"),
                ("pengaturan", "/admin/pengaturan", "Pengaturan", "atur"),
                ("backup", "/admin/backup", "Backup & Pulihkan", "backup"),
                ("publik", "/", "Halaman Publik", "globe"),
                ("keluar", "/keluar", "Keluar", "keluar"),
            ]
            item_bawah = [item_samping[0], item_samping[1], item_samping[2],
                          item_samping[3], item_samping[5]]
        else:
            nama_tampil = "Keluarga " + member["kepala_nama"]
            peran = "Jemaat"
            awal = (member["kepala_nama"] or "J").strip()[:1].upper()
            item_samping = [
                ("beranda", "/jemaat", "Beranda Jemaat", "beranda"),
                ("dokumen", "/jemaat#dokumen", "Dokumen Saya", "dokumen"),
                ("password", "/jemaat#ganti-password", "Ganti Password", "kunci"),
                ("publik", "/", "Halaman Publik", "globe"),
                ("keluar", "/keluar", "Keluar", "keluar"),
            ]
            item_bawah = [item_samping[0], item_samping[1], item_samping[2],
                          item_samping[4]]
        samping = "".join(_tautan_nav(it, aktif) for it in item_samping)
        bawah = "".join(_tautan_nav(it, aktif) for it in item_bawah)
        kerangka = f"""<div class="shell">
  <aside class="sidebar">
    <div class="brand">{logo_html(logo)}<div class="brand-teks"><b>{esc(church)}</b><small>Data Jemaat Gereja</small></div></div>
    <nav class="side-nav">{samping}</nav>
    <div class="side-user"><span class="avatar">{esc(awal)}</span><div><b>{esc(nama_tampil)}</b><small>{esc(peran)}</small></div></div>
  </aside>
  <div class="shell-main">
    <div class="topbar">{logo_html(logo, "logo-img kecil")}<b>{esc(church)}</b><span class="top-user">{esc(nama_tampil)}</span></div>
    <main>{flash_html}{body}</main>
    <footer class="kaki"><small>Direktori Keluarga Jemaat &bull; Detail lengkap hanya tampil dengan NIK yang tepat</small></footer>
  </div>
</div>
<nav class="bottom-nav">{bawah}</nav>"""
        return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - {esc(church)}</title>
<link rel="stylesheet" href="/static/style.css">{tema_style}{bg_style}</head>
<body class="{tema_kelas}">{kerangka}
<script src="/static/app.js"></script>{extra_script}
</body></html>"""
    # Tanpa sesi (halaman masuk & galat): kartu tengah polos bermerek.
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - {esc(church)}</title>
<link rel="stylesheet" href="/static/style.css">{tema_style}{bg_style}</head>
<body class="polos {tema_kelas}"><main class="polos-main">
<div class="brand-center">{logo_html(logo, "logo-img besar")}<div class="brand-nama-besar">{esc(church)}</div></div>
{flash_html}{body}</main>
<script src="/static/app.js"></script>{extra_script}
</body></html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DataJemaatGereja/1.0"

    def log_message(self, fmt, *args):  # lebih senyap
        pass

    # -- util ---------------------------------------------------------------
    def send_html(self, content, status=200, extra_headers=None):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, ctype, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path, flash="", set_cookie=None):
        headers = []
        if set_cookie:
            headers.append(("Set-Cookie", set_cookie))
        if flash:
            path += ("&" if "?" in path else "?") + "msg=" + urllib.parse.quote(flash)
        headers.append(("Location", path))
        self.send_html("", status=303, extra_headers=headers)

    def _sesi(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("jemaat_session")
        if not morsel:
            return None
        return LOGIN_SESSIONS.get(morsel.value)

    def current_user(self):
        """Baris users bila sesi ini sesi ADMIN; selain itu None.
        Sesi jemaat TIDAK PERNAH dianggap admin."""
        sesi = self._sesi()
        if not sesi or sesi.get("tipe") != "admin":
            return None
        conn = db()
        row = conn.execute("SELECT * FROM users WHERE id=?",
                           (sesi.get("user_id"),)).fetchone()
        conn.close()
        return row

    def current_member(self):
        """Baris families bila sesi ini sesi JEMAAT; selain itu None."""
        sesi = self._sesi()
        if not sesi or sesi.get("tipe") != "jemaat":
            return None
        conn = db()
        row = conn.execute("SELECT * FROM families WHERE id=?",
                           (sesi.get("family_id"),)).fetchone()
        conn.close()
        return row

    def church(self):
        conn = db()
        name = nama_gereja(conn)
        conn.close()
        return name

    def form(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = urllib.parse.parse_qs(raw)
        return {k: v[0] for k, v in parsed.items()}

    def parse_multipart(self, max_bytes=MAX_BODY_BYTES):
        """Parser multipart/form-data minimal (standard library saja).
        Kembalikan (fields, files); files: nama_field -> (nama_berkas, bytes)."""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(.+)", ctype)
        if not m:
            return {}, {}
        boundary = m.group(1).strip().strip('"')
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > max_bytes:
            raise ValueError("Ukuran unggahan melebihi batas yang diizinkan.")
        body = self.rfile.read(length) if length else b""
        delim = b"--" + boundary.encode("utf-8")
        fields, files = {}, {}
        for part in body.split(delim):
            if part.startswith(b"\r\n"):
                part = part[2:]
            if part.endswith(b"--"):
                part = part[:-2]
            if part.endswith(b"\r\n"):
                part = part[:-2]
            if not part or b"\r\n\r\n" not in part:
                continue
            head, data = part.split(b"\r\n\r\n", 1)
            if data.endswith(b"\r\n"):
                data = data[:-2]
            headers = head.decode("utf-8", "replace")
            name_m = re.search(r'name="([^"]+)"', headers)
            if not name_m:
                continue
            name = name_m.group(1)
            fn_m = re.search(r'filename="([^"]*)"', headers)
            if fn_m is not None:
                files[name] = (fn_m.group(1), data)
            else:
                fields[name] = data.decode("utf-8", "replace")
        return fields, files

    def read_form(self):
        """Baca POST body: multipart -> (fields, files); urlencoded -> (fields, {})."""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            return self.parse_multipart()
        return self.form(), {}

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        flash = (qs.get("msg") or [""])[0]
        try:
            self.route_get(path, qs, flash)
        except Exception as e:  # jangan bocorkan stack ke user, tapi catat di log server
            import traceback; traceback.print_exc()
            self.send_html(layout(self.current_user(), self.church(), "Error",
                                  f"<p>Terjadi kesalahan: {esc(e)}</p>"), status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.route_post(parsed.path)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.send_html(layout(self.current_user(), self.church(), "Error",
                                  f"<p>Terjadi kesalahan: {esc(e)}</p>"), status=500)

    # -- GET routes ---------------------------------------------------------
    def route_get(self, path, qs, flash):
        if path in ("/static/style.css", "/static/app.js"):
            fname = os.path.join(BASE_DIR, path.lstrip("/"))
            ctype = "text/css" if path.endswith(".css") else "application/javascript"
            with open(fname, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/foto/"):
            return self.serve_foto(path[len("/foto/"):])
        if path == "/":
            return self.home(qs, flash)
        if path == "/login":
            body = """<section class="panel kartu-masuk"><h2>Masuk Admin</h2>
            <form method="post" action="/login">
              <label>Username <input name="username" required autocomplete="username"></label>
              <label>Password <input name="password" type="password" required autocomplete="current-password"></label>
              <button type="submit">Masuk</button></form>
            <p><small>Akun uji lokal ada di README proyek. Jangan pakai akun awal di produksi.</small></p>
            <p><a href="/">Kembali ke halaman publik</a></p></section>"""
            return self.send_html(layout(None, self.church(), "Masuk", body, flash))
        if path in ("/logout", "/keluar"):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get("jemaat_session")
            if morsel:
                LOGIN_SESSIONS.pop(morsel.value, None)
            return self.redirect("/", "Sampai jumpa.",
                                 set_cookie="jemaat_session=; Max-Age=0; Path=/")
        if path == "/jemaat":
            return self.jemaat_home(flash)
        if path.startswith("/dokumen/"):
            return self.serve_dokumen(path[len("/dokumen/"):])

        user = self.current_user()
        if not user:
            return self.redirect("/login", "Silakan masuk dulu.")
        if path == "/admin":
            return self.admin_dashboard(user, qs, flash)
        if path == "/admin/keluarga":
            return self.admin_keluarga_list(user, qs, flash)
        if path == "/admin/keluarga/baru":
            return self.admin_keluarga_form(user, None, flash)
        if path == "/admin/pengaturan":
            return self.admin_pengaturan(user, flash)
        if path == "/admin/backup":
            return self.admin_backup(user, flash)
        if path == "/admin/backup/unduh":
            return self.admin_backup_unduh(user)
        if path.startswith("/admin/keluarga/"):
            try:
                fid = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self.not_found(user)
            return self.admin_keluarga_form(user, fid, flash)
        if path.startswith("/admin/anak/") and path.endswith("/edit"):
            try:
                cid = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self.not_found(user)
            return self.admin_anak_form(user, cid, flash)
        return self.not_found(user)

    def not_found(self, user):
        return self.send_html(layout(user, self.church(), "404",
                                     "<p>Halaman tidak ditemukan.</p>"), status=404)

    # -- POST routes --------------------------------------------------------
    def route_post(self, path):
        if path == "/login":
            return self.post_login()
        if path == "/login-jemaat":
            return self.post_login_jemaat()
        if path == "/jemaat/ganti-password":
            return self.post_jemaat_ganti_password()
        user = self.current_user()
        if not user:
            return self.redirect("/login", "Silakan masuk dulu.")
        if path == "/admin/backup/pulihkan":
            # Berkas backup bisa jauh lebih besar dari unggahan foto biasa,
            # jadi dibaca dengan batas ukuran khusus backup.
            try:
                _f, files = self.parse_multipart(MAX_BACKUP_BYTES)
            except ValueError as e:
                return self.redirect("/admin/backup", str(e))
            return self.post_backup_pulihkan(user, files)
        if path.startswith("/admin/keluarga/") and "/dokumen" in path:
            # Unggahan dokumen boleh sampai ~10 MB: batas request khusus.
            parts = path.split("/")
            try:
                fid_dok = int(parts[3])
                tujuan = f"/admin/keluarga/{fid_dok}"
            except (IndexError, ValueError):
                tujuan = "/admin"
            try:
                f_dok, files_dok = self.parse_multipart(MAX_DOK_BODY_BYTES)
            except ValueError as e:
                return self.redirect(tujuan, str(e))
            return self.dispatch_dokumen(user, path, f_dok, files_dok)
        f, files = self.read_form()
        if path == "/admin/pengaturan/warna":
            return self.post_warna_tema(user, f)
        if path == "/admin/pengaturan":
            return self.post_pengaturan(user, f)
        if path == "/admin/pengaturan/banner":
            return self.post_gambar_situs(user, "banner_foto", files, "Banner")
        if path == "/admin/pengaturan/banner-hapus":
            return self.post_gambar_situs_hapus(user, "banner_foto", "Banner")
        if path == "/admin/pengaturan/wallpaper":
            return self.post_gambar_situs(user, "wallpaper_foto", files, "Wallpaper")
        if path == "/admin/pengaturan/wallpaper-hapus":
            return self.post_gambar_situs_hapus(user, "wallpaper_foto", "Wallpaper")
        if path == "/admin/pengaturan/logo":
            return self.post_gambar_situs(user, "logo", files, "Logo")
        if path == "/admin/pengaturan/logo-hapus":
            return self.post_gambar_situs_hapus(user, "logo", "Logo")
        if path == "/ganti-password":
            return self.post_ganti_password(user, f)
        if path == "/admin/keluarga/baru":
            return self.post_keluarga_simpan(user, None, f, files)
        if path.startswith("/admin/keluarga/"):
            parts = path.split("/")
            try:
                fid = int(parts[3])
            except (IndexError, ValueError):
                return self.not_found(user)
            aksi = parts[4] if len(parts) > 4 else ""
            if aksi == "edit":
                return self.post_keluarga_simpan(user, fid, f, files)
            if aksi == "hapus":
                return self.post_keluarga_hapus(user, fid)
            if aksi == "foto-hapus":
                return self.post_keluarga_foto_hapus(user, fid)
            if aksi == "anak":
                return self.post_anak_tambah(user, fid, f, files)
        if path.startswith("/admin/anak/"):
            parts = path.split("/")
            try:
                cid = int(parts[3])
            except (IndexError, ValueError):
                return self.not_found(user)
            aksi = parts[4] if len(parts) > 4 else ""
            if aksi == "edit":
                return self.post_anak_edit(user, cid, f, files)
            if aksi == "hapus":
                return self.post_anak_hapus(user, cid)
            if aksi == "naik":
                return self.post_anak_pindah(user, cid, -1)
            if aksi == "turun":
                return self.post_anak_pindah(user, cid, +1)
        return self.not_found(user)

    # -- berkas foto ----------------------------------------------------------
    def serve_foto(self, raw_name):
        """Foto hanya disajikan lewat route ini, dengan nama berkas token acak.
        Tidak ada directory listing; nama di luar pola token ditolak.
        DAFTAR PUTIH: nama juga harus tercatat sebagai foto sungguhan —
        foto nikah keluarga, foto anak, atau banner/wallpaper/logo situs.
        Berkas dokumen (pola "dok-...") tidak pernah lolos route ini."""
        name = urllib.parse.unquote(raw_name)
        if not FOTO_NAME_RE.fullmatch(name):
            return self.send_html("Tidak ditemukan.", status=404)
        conn = db()
        tercatat = conn.execute(
            """SELECT 1 WHERE EXISTS(SELECT 1 FROM families WHERE foto_nikah=?)
               OR EXISTS(SELECT 1 FROM children WHERE foto=?)
               OR EXISTS(SELECT 1 FROM settings
                         WHERE key IN ('banner_foto','wallpaper_foto','logo')
                           AND value=?)""",
            (name, name, name)).fetchone()
        conn.close()
        if not tercatat:
            return self.send_html("Tidak ditemukan.", status=404)
        fpath = os.path.join(UPLOADS_DIR, name)
        if not os.path.isfile(fpath):
            return self.send_html("Tidak ditemukan.", status=404)
        ext = os.path.splitext(name)[1].lower()
        ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".webp": "image/webp"}[ext]
        with open(fpath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    # -- auth ---------------------------------------------------------------
    def post_login(self):
        f = self.form()
        conn = db()
        row = conn.execute("SELECT * FROM users WHERE username=?",
                           (f.get("username", "").strip(),)).fetchone()
        conn.close()
        if row and verify_password(f.get("password", ""), row["password_hash"], row["salt"]):
            token = secrets.token_urlsafe(32)
            LOGIN_SESSIONS[token] = {"tipe": "admin", "user_id": row["id"]}
            return self.redirect("/admin", f"Selamat datang, {row['nama']}.",
                                 set_cookie=f"jemaat_session={token}; HttpOnly; Path=/; SameSite=Lax")
        return self.redirect("/login", "Username atau password salah.")

    # -----------------------------------------------------------------------
    # PUBLIK: pencarian jemaat
    # -----------------------------------------------------------------------
    def home(self, qs, flash):
        # Halaman publik yang sangat minimal: banner (bila diatur admin),
        # judul, SATU formulir pencarian (input "q" + tombol "Cari"), dan
        # hasil. Tanpa nav, tanpa header nama gereja, tanpa footer.
        # Pencarian terpadu: q yang tepat 16 digit angka -> alur NIK persis
        # (kartu lengkap); selain itu -> alur nama sebagian (daftar minimal).
        conn = db()
        church = nama_gereja(conn)
        hasil = ""
        q = (qs.get("q") or [""])[0].strip()
        if q:
            if valid_nik(q):
                fam = conn.execute(
                    "SELECT * FROM families WHERE kepala_nik=? OR pasangan_nik=?",
                    (q, q)).fetchone()
                if fam:
                    anak = conn.execute(
                        "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id",
                        (fam["id"],)).fetchall()
                    hasil = self.kartu_keluarga_lengkap(fam, anak)
                else:
                    hasil = ('<div class="flash warn">Data keluarga dengan NIK itu tidak '
                             'ditemukan. Pastikan NIK sudah benar (16 digit, milik kepala '
                             'keluarga atau pasangan), atau hubungi admin/pengurus gereja.</div>')
            elif len(q) < 2:
                hasil = ('<div class="flash warn">Ketik minimal 2 huruf dari nama kepala '
                         'keluarga atau pasangan.</div>')
            else:
                rows = conn.execute(
                    """SELECT kepala_nama, pasangan_nama, status FROM families
                       WHERE kepala_nama LIKE ? OR pasangan_nama LIKE ?
                       ORDER BY kepala_nama LIMIT 20""",
                    (f"%{q}%", f"%{q}%")).fetchall()
                if rows:
                    items = "".join(
                        f"""<li><b>{esc(r['kepala_nama'])}</b>
                        &mdash; pasangan: {esc(r['pasangan_nama'] or '-')}
                        &mdash; status: {esc(r['status'])}<br>
                        <small>Untuk melihat foto dan detail lengkap keluarga ini,
                        cari dengan <b>NIK</b> kepala keluarga / pasangan.</small></li>"""
                        for r in rows)
                    hasil = (f'<section class="panel"><h3>Hasil pencarian nama</h3>'
                             f'<ul>{items}</ul></section>')
                else:
                    hasil = ('<div class="flash warn">Tidak ada keluarga dengan nama itu. '
                             'Coba ejaan lain, atau cari langsung dengan NIK.</div>')
        # Bila pencarian barusan dilakukan (ada hasil/pesan yang tampil):
        # beri tautan "Cari Lagi" di bawah hasil, dan siapkan skrip kecil
        # history.replaceState yang menghapus ?q=... dari address bar TANPA
        # memuat ulang halaman. Hasil tetap terlihat, tetapi REFRESH
        # sesudahnya membuka halaman awal yang polos — bukan mengulang
        # pencarian. Halaman polos (tanpa q) tidak mendapat skrip ini.
        skrip_refresh = ""
        if hasil:
            hasil += '<p class="cari-lagi"><a href="/">↺ Cari Lagi</a></p>'
            skrip_refresh = "<script>history.replaceState(null, '', '/');</script>"
        banner = get_setting(conn, "banner_foto", "")
        wallpaper = get_setting(conn, "wallpaper_foto", "")
        logo = get_setting(conn, "logo", "")
        conn.close()
        banner_html = (f'<img class="banner" src="/foto/{esc(banner)}" '
                       f'alt="Banner">' if banner else "")
        brand_html = f'<div class="brand-publik">{logo_html(logo, "logo-img besar")}</div>'
        login_jemaat = """<section class="panel kartu-login-jemaat"><h3>Login Jemaat</h3>
        <p><small>Masuk dengan NIK kepala keluarga untuk melihat data keluarga
        &amp; mengunduh dokumen Anda.</small></p>
        <form method="post" action="/login-jemaat">
          <label>NIK Kepala Keluarga <input name="nik" inputmode="numeric"
            maxlength="16" required autocomplete="username"></label>
          <label>Password <input name="password" type="password" required
            autocomplete="current-password"></label>
          <button type="submit">Masuk</button></form></section>"""
        body = f"""{banner_html}{brand_html}
        <section class="panel kartu-cari"><h2>Cari Keluarga (Nama/NIK)</h2>
        <form method="get" action="/">
          <input name="q" autocomplete="off">
          <button type="submit">Cari</button></form></section>
        {hasil}
        {login_jemaat}"""
        return self.send_html(layout(None, church, "Cari Keluarga (Nama/NIK)", body, flash,
                                     bg=wallpaper or None, publik=True,
                                     extra_script=skrip_refresh))

    def kartu_keluarga_lengkap(self, fam, anak):
        """Kartu lengkap — HANYA dirender untuk pencarian NIK yang tepat.
        NIK sendiri tidak ikut ditampilkan di halaman publik."""
        foto_html = (f'<img class="foto-nikah" src="/foto/{esc(fam["foto_nikah"])}" '
                     f'alt="Foto pernikahan keluarga {esc(fam["kepala_nama"])}">'
                     if fam["foto_nikah"] else
                     '<p class="tanpa-foto">Foto pernikahan belum tersedia.</p>')
        if anak:
            items = ""
            for i, a in enumerate(anak, start=1):
                img = (f'<img class="foto-anak" src="/foto/{esc(a["foto"])}" '
                       f'alt="Foto {esc(a["nama"])}">' if a["foto"] else "")
                items += f'<li><span class="anak-label">Anak {i}</span> {img} {esc(a["nama"])}</li>'
            anak_html = f"<h4>Anak-anak</h4><ul class='anak-list'>{items}</ul>"
        else:
            anak_html = "<h4>Anak-anak</h4><p>Belum ada data anak.</p>"
        tgl = format_tanggal(fam["tanggal_menikah"]) if fam["tanggal_menikah"] else "-"
        return f"""<section class="panel kartu-keluarga"><h3>Keluarga {esc(fam['kepala_nama'])}</h3>
        {foto_html}
        <p><b>Kepala Keluarga:</b> {esc(fam['kepala_nama'])}</p>
        <p><b>Pasangan:</b> {esc(fam['pasangan_nama'] or '-')}</p>
        <p><b>Status Perkawinan:</b> {esc(fam['status'])}</p>
        <p><b>Tanggal Menikah:</b> {esc(tgl)}</p>
        {anak_html}</section>"""

    # -----------------------------------------------------------------------
    # JEMAAT: login anggota & halaman keluarga sendiri
    # -----------------------------------------------------------------------
    def post_login_jemaat(self):
        f = self.form()
        nik = re.sub(r"\D", "", f.get("nik") or "")
        conn = db()
        fam = conn.execute("SELECT * FROM families WHERE kepala_nik=?",
                           (nik,)).fetchone()
        conn.close()
        if not fam:
            return self.redirect("/", "NIK atau password salah.")
        if not fam["kata_sandi_hash"]:
            return self.redirect(
                "/", "Login jemaat untuk keluarga ini belum diaktifkan. "
                     "Hubungi admin gereja.")
        if verify_password(f.get("password", ""), fam["kata_sandi_hash"],
                           fam["kata_sandi_salt"]):
            token = secrets.token_urlsafe(32)
            LOGIN_SESSIONS[token] = {"tipe": "jemaat", "family_id": fam["id"]}
            return self.redirect(
                "/jemaat", f"Selamat datang, keluarga {fam['kepala_nama']}.",
                set_cookie=f"jemaat_session={token}; HttpOnly; Path=/; SameSite=Lax")
        return self.redirect("/", "NIK atau password salah.")

    def jemaat_home(self, flash):
        member = self.current_member()
        if not member:
            if self.current_user():
                return self.redirect("/admin")
            return self.redirect("/", "Silakan masuk dengan NIK & password dulu.")
        conn = db()
        church = nama_gereja(conn)
        anak = conn.execute(
            "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id",
            (member["id"],)).fetchall()
        doks = conn.execute(
            "SELECT d.*, c.nama AS nama_anak FROM dokumen d "
            "LEFT JOIN children c ON c.id=d.child_id "
            "WHERE d.family_id=? ORDER BY d.id", (member["id"],)).fetchall()
        conn.close()
        if doks:
            baris = ""
            for d in doks:
                untuk = (f"Anak: {esc(d['nama_anak'])}" if d["child_id"]
                         else "Keluarga")
                baris += f"""<li><b>{esc(DOK_LABEL.get(d['jenis'], d['jenis']))}</b>
                &mdash; {untuk}<br><small>{esc(d['original_name'])}</small><br>
                <a href="/dokumen/{d['id']}"><button type="button">Download</button></a></li>"""
            dok_html = f'<ul class="dok-list">{baris}</ul>'
        else:
            dok_html = ("<p>Belum ada dokumen yang diunggah admin untuk "
                        "keluarga ini.</p>")
        body = f"""<h2>Beranda Jemaat</h2>
        {self.kartu_keluarga_lengkap(member, anak)}
        <section class="panel" id="dokumen"><h3>Dokumen Saya</h3>
        {dok_html}</section>
        <section class="panel" id="ganti-password"><h3>Ganti Password</h3>
        <form method="post" action="/jemaat/ganti-password">
          <label>Password lama <input name="password_lama" type="password"
            required autocomplete="current-password"></label>
          <label>Password baru (minimal 8 karakter) <input name="password_baru"
            type="password" required minlength="8" autocomplete="new-password"></label>
          <button type="submit">Ganti Password</button></form></section>"""
        return self.send_html(layout(None, church, "Beranda Jemaat", body, flash,
                                     member=member, aktif="beranda"))

    def post_jemaat_ganti_password(self):
        member = self.current_member()
        if not member:
            return self.redirect("/", "Silakan masuk dengan NIK & password dulu.")
        f = self.form()
        baru = f.get("password_baru") or ""
        if not verify_password(f.get("password_lama", ""),
                               member["kata_sandi_hash"], member["kata_sandi_salt"]):
            return self.redirect("/jemaat", "Password lama salah.")
        if len(baru) < 8:
            return self.redirect("/jemaat", "Password baru minimal 8 karakter.")
        ph, salt = hash_password(baru)
        conn = db()
        conn.execute("UPDATE families SET kata_sandi_hash=?, kata_sandi_salt=? "
                     "WHERE id=?", (ph, salt, member["id"]))
        conn.commit()
        conn.close()
        return self.redirect("/jemaat", "Password berhasil diganti.")

    # -----------------------------------------------------------------------
    # DOKUMEN: unduh (admin: semua; jemaat: hanya milik keluarganya)
    # -----------------------------------------------------------------------
    def serve_dokumen(self, raw_id):
        try:
            did = int(raw_id)
        except (TypeError, ValueError):
            return self.send_html("Tidak ditemukan.", status=404)
        conn = db()
        dok = conn.execute("SELECT * FROM dokumen WHERE id=?", (did,)).fetchone()
        conn.close()
        if not dok or not DOK_NAME_RE.fullmatch(dok["stored_name"] or ""):
            return self.send_html("Tidak ditemukan.", status=404)
        user = self.current_user()
        member = self.current_member()
        boleh = user is not None or (member is not None
                                     and member["id"] == dok["family_id"])
        if not boleh:
            return self.send_html("Tidak ditemukan.", status=404)
        fpath = os.path.join(UPLOADS_DIR, dok["stored_name"])
        if not os.path.isfile(fpath):
            return self.send_html("Tidak ditemukan.", status=404)
        ext = os.path.splitext(dok["stored_name"])[1].lower()
        ctype = {".pdf": "application/pdf", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp"}[ext]
        with open(fpath, "rb") as fh:
            data = fh.read()
        nama_asli = (dok["original_name"] or dok["stored_name"]).replace('"', "")
        nama_asli = nama_asli.replace("\r", "").replace("\n", "")
        return self.send_bytes(data, ctype, filename=nama_asli)

    def dispatch_dokumen(self, user, path, f, files):
        """Pengarah POST dokumen dari route_post (sudah lolos penjaga admin
        dan parser multipart khusus dokumen)."""
        parts = path.split("/")
        # /admin/keluarga/<fid>/...
        try:
            fid = int(parts[3])
        except (IndexError, ValueError):
            return self.not_found(user)
        sisa = parts[4:]
        if sisa == ["dokumen"]:
            return self.post_dokumen_simpan(user, fid, None, f, files)
        if len(sisa) == 3 and sisa[0] == "dokumen" and sisa[2] == "hapus":
            try:
                did = int(sisa[1])
            except ValueError:
                return self.not_found(user)
            return self.post_dokumen_hapus(user, fid, None, did)
        if len(sisa) >= 2 and sisa[0] == "anak":
            try:
                cid = int(sisa[1])
            except ValueError:
                return self.not_found(user)
            if sisa[2:] == ["dokumen"]:
                return self.post_dokumen_simpan(user, fid, cid, f, files)
            if len(sisa) == 5 and sisa[2] == "dokumen" and sisa[4] == "hapus":
                try:
                    did = int(sisa[3])
                except ValueError:
                    return self.not_found(user)
                return self.post_dokumen_hapus(user, fid, cid, did)
        return self.not_found(user)

    def post_dokumen_simpan(self, user, fid, cid, f, files):
        """Unggah/ganti satu dokumen. cid=None -> dokumen tingkat keluarga;
        cid terisi -> dokumen milik anak itu. Satu slot (jenis) per
        keluarga/anak: mengunggah ulang menggantikan berkas lama."""
        back = f"/admin/keluarga/{fid}"
        jenis = (f.get("jenis") or "").strip()
        sah = DOK_ANAK if cid is not None else DOK_KELUARGA
        if jenis not in sah:
            return self.redirect(back, "Jenis dokumen tidak dikenal.")
        conn = db()
        fam = conn.execute("SELECT id FROM families WHERE id=?", (fid,)).fetchone()
        anak = None
        if cid is not None:
            anak = conn.execute(
                "SELECT id FROM children WHERE id=? AND family_id=?",
                (cid, fid)).fetchone()
        if not fam or (cid is not None and not anak):
            conn.close()
            return self.not_found(user)
        stored, original, err = save_dokumen(files, "dokumen")
        if err:
            conn.close()
            return self.redirect(back, err)
        if not stored:
            conn.close()
            return self.redirect(back, "Pilih berkas dokumen dulu.")
        if cid is not None:
            lama = conn.execute(
                "SELECT * FROM dokumen WHERE child_id=? AND jenis=?",
                (cid, jenis)).fetchone()
        else:
            lama = conn.execute(
                "SELECT * FROM dokumen WHERE family_id=? AND child_id IS NULL "
                "AND jenis=?", (fid, jenis)).fetchone()
        dibuat = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            if lama:
                conn.execute(
                    "UPDATE dokumen SET stored_name=?, original_name=?, dibuat=? "
                    "WHERE id=?", (stored, original, dibuat, lama["id"]))
            else:
                conn.execute(
                    "INSERT INTO dokumen (family_id, child_id, jenis, stored_name, "
                    "original_name, dibuat) VALUES (?,?,?,?,?,?)",
                    (fid, cid, jenis, stored, original, dibuat))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            delete_upload(stored)
            return self.redirect(back, "Dokumen gagal disimpan (duplikat).")
        conn.close()
        if lama and lama["stored_name"] != stored:
            delete_upload(lama["stored_name"])
        return self.redirect(back, f"Dokumen {DOK_LABEL[jenis]} disimpan.")

    def post_dokumen_hapus(self, user, fid, cid, did):
        back = f"/admin/keluarga/{fid}"
        conn = db()
        if cid is not None:
            dok = conn.execute(
                "SELECT * FROM dokumen WHERE id=? AND family_id=? AND child_id=?",
                (did, fid, cid)).fetchone()
        else:
            dok = conn.execute(
                "SELECT * FROM dokumen WHERE id=? AND family_id=? "
                "AND child_id IS NULL", (did, fid)).fetchone()
        if not dok:
            conn.close()
            return self.not_found(user)
        conn.execute("DELETE FROM dokumen WHERE id=?", (did,))
        conn.commit()
        conn.close()
        delete_upload(dok["stored_name"])
        return self.redirect(back, "Dokumen dihapus.")

    # -----------------------------------------------------------------------
    # ADMIN
    # -----------------------------------------------------------------------
    def admin_dashboard(self, user, qs, flash):
        # Dashboard ringkas: peringatan password, SATU kartu statistik
        # gabungan (Keluarga + Anak) yang bisa diklik menuju daftar
        # keluarga, tombol tambah, dan formulir pencarian (hasilnya
        # tampil di /admin/keluarga). Daftar keluarga TIDAK lagi tampil
        # di sini — pindah utuh ke /admin/keluarga.
        conn = db()
        church = nama_gereja(conn)
        total_keluarga = conn.execute("SELECT COUNT(*) c FROM families").fetchone()["c"]
        total_anak = conn.execute("SELECT COUNT(*) c FROM children").fetchone()["c"]
        me = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        conn.close()
        warn = ""
        if me and verify_password("admin123", me["password_hash"], me["salt"]):
            warn = ('<div class="flash warn">PERINGATAN: akun admin ini masih memakai '
                    'password awal lokal. Segera ganti lewat menu Pengaturan '
                    'sebelum dipakai beneran.</div>')
        body = f"""<h2>Dashboard Admin</h2>{warn}
        <a class="card stat-gabung" href="/admin/keluarga">
          <span class="stat-isi"><b>{total_keluarga}</b> Keluarga
            <span class="stat-titik">&bull;</span> <b>{total_anak}</b> Anak</span>
          <span class="stat-buka">Lihat daftar &rarr;</span></a>
        <p><a href="/admin/keluarga/baru"><button type="button">+ Tambah Keluarga</button></a></p>
        <form method="get" action="/admin/keluarga">
          <label>Cari keluarga (nama / NIK / nama anak) <input name="q" autocomplete="off"></label>
          <button type="submit">Cari</button></form>"""
        return self.send_html(layout(user, church, "Dashboard Admin", body, flash,
                                     aktif="dashboard"))

    @staticmethod
    def _lencana_keluarga(r):
        """Lencana di baris daftar keluarga: jumlah dokumen + status
        login jemaat."""
        b = ""
        if r["jml_dok"]:
            b += f' <span class="lencana">Dokumen: {r["jml_dok"]}</span>'
        if r["kata_sandi_hash"]:
            b += ' <span class="lencana emas">Login jemaat aktif</span>'
        return b

    def admin_keluarga_list(self, user, qs, flash):
        # Halaman daftar keluarga (dipindah dari dashboard). Pencarian q
        # mencocokkan sebagian nama kepala/pasangan, NIK kepala/pasangan,
        # DAN nama anak: keluarga yang ANAKNYA cocok ikut tampil, dengan
        # baris "Anak yang cocok: ..." di bawah baris keluarganya.
        conn = db()
        church = nama_gereja(conn)
        q = (qs.get("q") or [""])[0].strip()
        kolom_tambahan = (", (SELECT COUNT(*) FROM children c WHERE c.family_id=f.id) jml_anak"
                          ", (SELECT COUNT(*) FROM dokumen d WHERE d.family_id=f.id) jml_dok")
        cocok_anak = {}
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                f"""SELECT f.* {kolom_tambahan}
                   FROM families f
                   WHERE f.kepala_nama LIKE ? OR f.pasangan_nama LIKE ?
                      OR f.kepala_nik LIKE ? OR f.pasangan_nik LIKE ?
                      OR EXISTS (SELECT 1 FROM children ca
                                 WHERE ca.family_id=f.id AND ca.nama LIKE ?)
                   ORDER BY f.kepala_nama LIMIT 100""",
                (like, like, like, like, like)).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                tanda = ",".join("?" * len(ids))
                for c in conn.execute(
                        f"""SELECT family_id, nama FROM children
                            WHERE family_id IN ({tanda}) AND nama LIKE ?
                            ORDER BY urutan, id""",
                        (*ids, like)).fetchall():
                    cocok_anak.setdefault(c["family_id"], []).append(c["nama"])
        else:
            rows = conn.execute(
                f"""SELECT f.* {kolom_tambahan}
                   FROM families f ORDER BY f.kepala_nama LIMIT 100""").fetchall()
        conn.close()

        def baris(r):
            h = f"""<li><a href="/admin/keluarga/{r['id']}"><b>{esc(r['kepala_nama'])}</b></a>
            &mdash; pasangan: {esc(r['pasangan_nama'] or '-')}
            &mdash; {esc(r['status'])} &mdash; {r['jml_anak']} anak{self._lencana_keluarga(r)}
            <br><small>NIK kepala: <code>{esc(r['kepala_nik'])}</code></small>"""
            if cocok_anak.get(r["id"]):
                nama_cocok = ", ".join(esc(n) for n in cocok_anak[r["id"]])
                h += (f'<br><small class="anak-cocok">Anak yang cocok: '
                      f'{nama_cocok}</small>')
            return h + "</li>"

        if rows:
            daftar = f"<ul>{''.join(baris(r) for r in rows)}</ul>"
        elif q:
            daftar = f'<p>Tidak ada keluarga yang cocok dengan "{esc(q)}".</p>'
        else:
            daftar = "<p>Belum ada data keluarga.</p>"
        body = f"""<h2>Daftar Keluarga</h2>
        <p><a href="/admin/keluarga/baru"><button type="button">+ Tambah Keluarga</button></a></p>
        <form method="get" action="/admin/keluarga">
          <label>Cari keluarga (nama / NIK / nama anak) <input name="q" value="{esc(q)}" autocomplete="off"></label>
          <button type="submit">Cari</button></form>
        {daftar}"""
        return self.send_html(layout(user, church, "Daftar Keluarga", body, flash,
                                     aktif="dashboard"))

    def admin_keluarga_form(self, user, fid, flash):
        conn = db()
        church = nama_gereja(conn)
        fam = None
        anak = []
        dok_keluarga = {}
        dok_anak = {}
        if fid is not None:
            fam = conn.execute("SELECT * FROM families WHERE id=?", (fid,)).fetchone()
            if not fam:
                conn.close()
                return self.not_found(user)
            anak = conn.execute(
                "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id", (fid,)).fetchall()
            for d in conn.execute("SELECT * FROM dokumen WHERE family_id=?",
                                  (fid,)).fetchall():
                if d["child_id"]:
                    dok_anak.setdefault(d["child_id"], {})[d["jenis"]] = d
                else:
                    dok_keluarga[d["jenis"]] = d
        conn.close()
        v = dict(fam) if fam else {}
        status_opts = "".join(
            f'<option value="{esc(s)}"{" selected" if v.get("status") == s else ""}>{esc(s)}</option>'
            for s in STATUS_PERKAWINAN)
        if fam:
            judul, aksi = f"Ubah Keluarga: {v['kepala_nama']}", f"/admin/keluarga/{fid}/edit"
        else:
            judul, aksi = "Tambah Keluarga", "/admin/keluarga/baru"
        foto_html = ""
        if fam and fam["foto_nikah"]:
            foto_html = f"""<p>Foto pernikahan saat ini:<br>
            <img class="foto-nikah" src="/foto/{esc(fam['foto_nikah'])}" alt="Foto pernikahan"></p>"""
        if fam and fam["kata_sandi_hash"]:
            status_login = ('<p><small>Status login jemaat: <b>AKTIF</b> — keluarga '
                            'ini bisa masuk lewat formulir Login Jemaat di halaman '
                            'publik dengan NIK kepala keluarga.</small></p>')
        elif fam:
            status_login = ('<p><small>Status login jemaat: belum aktif. Isi '
                            'password di bawah untuk mengaktifkannya.</small></p>')
        else:
            status_login = ('<p><small>Isi password bila keluarga ini boleh '
                            'masuk lewat Login Jemaat; kosongkan bila tidak.</small></p>')
        body = f"""<h2>{esc(judul)}</h2>
        <form method="post" action="{aksi}" enctype="multipart/form-data">
          <label>Nama Kepala Keluarga <input name="kepala_nama" required
            value="{esc(v.get('kepala_nama', ''))}"></label>
          <label>NIK Kepala Keluarga (16 digit) <input name="kepala_nik" required
            inputmode="numeric" maxlength="16" value="{esc(v.get('kepala_nik', ''))}"></label>
          <label>Nama Pasangan <input name="pasangan_nama"
            value="{esc(v.get('pasangan_nama', ''))}"></label>
          <label>NIK Pasangan (boleh kosong, bila diisi 16 digit) <input name="pasangan_nik"
            inputmode="numeric" maxlength="16" value="{esc(v.get('pasangan_nik', '') or '')}"></label>
          <label>Status Perkawinan <select name="status">{status_opts}</select></label>
          <label>Tanggal Menikah <input name="tanggal_menikah" type="date"
            value="{esc(v.get('tanggal_menikah', '') or '')}"></label>
          <label>Password Login Jemaat (isi untuk mengaktifkan / mengganti;
            kosongkan bila tidak diubah) <input name="kata_sandi" type="password"
            autocomplete="new-password"></label>
          {status_login}
          {foto_html}
          <label>Foto Pernikahan (JPG/PNG/WebP, maks 5 MB; kosongkan bila tidak diganti)
            <input name="foto_nikah" type="file" accept=".jpg,.jpeg,.png,.webp"></label>
          <button type="submit">Simpan Keluarga</button></form>"""
        if fam:
            def slot_dok(jenis, label, dok, url_unggah, url_hapus):
                """Satu slot dokumen: berkas saat ini + unduh/hapus, atau
                formulir unggah. Mengunggah pada slot yang sudah terisi
                berarti mengganti berkasnya."""
                if dok:
                    return f"""<div class="dok-slot"><p><b>{esc(label)}</b><br>
                    <small>Berkas: {esc(dok['original_name'])}</small><br>
                    <a href="/dokumen/{dok['id']}"><button type="button">Download</button></a></p>
                    <form method="post" action="{url_unggah}" enctype="multipart/form-data">
                      <input type="hidden" name="jenis" value="{jenis}">
                      <label>Ganti berkas (PDF/JPG/PNG/WebP, maks 10 MB)
                        <input name="dokumen" type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" required></label>
                      <button type="submit">Ganti Dokumen</button></form>
                    <form method="post" action="{url_hapus}"
                      onsubmit="return confirm('Hapus dokumen ini?')">
                      <button type="submit" class="danger">Hapus Dokumen</button></form></div>"""
                return f"""<div class="dok-slot"><p><b>{esc(label)}</b><br>
                <small>Belum ada berkas.</small></p>
                <form method="post" action="{url_unggah}" enctype="multipart/form-data">
                  <input type="hidden" name="jenis" value="{jenis}">
                  <label>Unggah berkas (PDF/JPG/PNG/WebP, maks 10 MB)
                    <input name="dokumen" type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" required></label>
                  <button type="submit">Unggah Dokumen</button></form></div>"""

            if fam["foto_nikah"]:
                body += f"""<form method="post" action="/admin/keluarga/{fid}/foto-hapus"
                  onsubmit="return confirm('Hapus foto pernikahan keluarga ini?')">
                  <button type="submit" class="danger">Hapus Foto Pernikahan</button></form>"""
            rows = ""
            for i, a in enumerate(anak):
                img = (f'<img class="foto-anak" src="/foto/{esc(a["foto"])}" alt="">'
                       if a["foto"] else "")
                slot_anak = ""
                for jenis, label in DOK_ANAK.items():
                    dok = dok_anak.get(a["id"], {}).get(jenis)
                    url_hapus = (f"/admin/keluarga/{fid}/anak/{a['id']}/dokumen/"
                                 f"{dok['id']}/hapus" if dok else "")
                    slot_anak += slot_dok(
                        jenis, label, dok,
                        f"/admin/keluarga/{fid}/anak/{a['id']}/dokumen", url_hapus)
                rows += f"""<li><div><span class="anak-label">Anak {i + 1}</span> {img} {esc(a['nama'])}
                  &mdash; <a href="/admin/anak/{a['id']}/edit">Ubah</a>
                  <form method="post" action="/admin/anak/{a['id']}/naik" class="inline">
                    <button type="submit">Naik</button></form>
                  <form method="post" action="/admin/anak/{a['id']}/turun" class="inline">
                    <button type="submit">Turun</button></form>
                  <form method="post" action="/admin/anak/{a['id']}/hapus" class="inline"
                    onsubmit="return confirm('Hapus anak ini beserta dokumennya?')">
                    <button type="submit" class="danger">Hapus</button></form></div>
                  <div class="dok-anak">{slot_anak}</div></li>"""
            slot_keluarga = ""
            for jenis, label in DOK_KELUARGA.items():
                dok = dok_keluarga.get(jenis)
                url_hapus = (f"/admin/keluarga/{fid}/dokumen/{dok['id']}/hapus"
                             if dok else "")
                slot_keluarga += slot_dok(
                    jenis, label, dok, f"/admin/keluarga/{fid}/dokumen", url_hapus)
            body += f"""<section class="panel"><h3>Anak-anak</h3>
            <ul class="anak-list">{rows or '<li>Belum ada anak.</li>'}</ul>
            <h4>Tambah Anak</h4>
            <form method="post" action="/admin/keluarga/{fid}/anak" enctype="multipart/form-data">
              <label>Nama Anak <input name="nama" required></label>
              <label>Foto Anak (boleh kosong) <input name="foto" type="file"
                accept=".jpg,.jpeg,.png,.webp"></label>
              <button type="submit">Tambah Anak</button></form></section>
            <section class="panel"><h3>Dokumen Keluarga</h3>
            <p><small>Dokumen resmi keluarga. Jemaat yang sudah diaktifkan
            login-nya dapat mengunduh dokumen ini dari halaman jemaatnya.</small></p>
            {slot_keluarga}</section>
            <form method="post" action="/admin/keluarga/{fid}/hapus"
              onsubmit="return confirm('Hapus SELURUH data keluarga ini beserta anak-anak dan dokumennya?')">
              <button type="submit" class="danger">Hapus Keluarga Ini</button></form>"""
        return self.send_html(layout(user, church, judul, body, flash,
                                     aktif="tambah" if not fam else "dashboard"))

    def post_keluarga_simpan(self, user, fid, f, files):
        back = f"/admin/keluarga/{fid}" if fid else "/admin/keluarga/baru"
        kepala_nama = (f.get("kepala_nama") or "").strip()
        kepala_nik = re.sub(r"\D", "", f.get("kepala_nik") or "")
        pasangan_nama = (f.get("pasangan_nama") or "").strip()
        pasangan_nik = re.sub(r"\D", "", f.get("pasangan_nik") or "")
        status = f.get("status") or "Menikah"
        tanggal = (f.get("tanggal_menikah") or "").strip() or None
        kata_sandi = f.get("kata_sandi") or ""
        if kata_sandi and len(kata_sandi) < 6:
            return self.redirect(back, "Password login jemaat minimal 6 karakter.")
        ks_hash = ks_salt = None
        if kata_sandi:
            ks_hash, ks_salt = hash_password(kata_sandi)
        if not kepala_nama:
            return self.redirect(back, "Nama kepala keluarga wajib diisi.")
        if not valid_nik(kepala_nik):
            return self.redirect(back, "NIK kepala keluarga harus tepat 16 digit angka.")
        if pasangan_nik and not valid_nik(pasangan_nik):
            return self.redirect(back, "NIK pasangan harus tepat 16 digit angka (atau kosongkan).")
        if pasangan_nik and pasangan_nik == kepala_nik:
            return self.redirect(back, "NIK pasangan tidak boleh sama dengan NIK kepala keluarga.")
        if status not in STATUS_PERKAWINAN:
            return self.redirect(back, "Status perkawinan tidak dikenal.")
        conn = db()
        if nik_terpakai(conn, kepala_nik, exclude_family_id=fid):
            conn.close()
            return self.redirect(back, "NIK kepala keluarga itu sudah terdaftar pada keluarga lain.")
        if pasangan_nik and nik_terpakai(conn, pasangan_nik, exclude_family_id=fid):
            conn.close()
            return self.redirect(back, "NIK pasangan itu sudah terdaftar pada keluarga lain.")
        foto_baru, err = save_upload(files, "foto_nikah")
        if err:
            conn.close()
            return self.redirect(back, err)
        try:
            if fid is None:
                cur = conn.execute(
                    """INSERT INTO families (kepala_nama, kepala_nik, pasangan_nama,
                       pasangan_nik, status, tanggal_menikah, foto_nikah,
                       kata_sandi_hash, kata_sandi_salt)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (kepala_nama, kepala_nik, pasangan_nama, pasangan_nik or None,
                     status, tanggal, foto_baru, ks_hash, ks_salt))
                conn.commit()
                fid_baru = cur.lastrowid
                conn.close()
                return self.redirect(f"/admin/keluarga/{fid_baru}",
                                     "Keluarga ditambahkan. Silakan tambah anak-anaknya di bawah.")
            lama = conn.execute("SELECT foto_nikah FROM families WHERE id=?", (fid,)).fetchone()
            if not lama:
                conn.close()
                return self.not_found(user)
            foto_final = foto_baru if foto_baru else lama["foto_nikah"]
            if ks_hash:
                conn.execute(
                    """UPDATE families SET kepala_nama=?, kepala_nik=?, pasangan_nama=?,
                       pasangan_nik=?, status=?, tanggal_menikah=?, foto_nikah=?,
                       kata_sandi_hash=?, kata_sandi_salt=? WHERE id=?""",
                    (kepala_nama, kepala_nik, pasangan_nama, pasangan_nik or None,
                     status, tanggal, foto_final, ks_hash, ks_salt, fid))
            else:
                conn.execute(
                    """UPDATE families SET kepala_nama=?, kepala_nik=?, pasangan_nama=?,
                       pasangan_nik=?, status=?, tanggal_menikah=?, foto_nikah=? WHERE id=?""",
                    (kepala_nama, kepala_nik, pasangan_nama, pasangan_nik or None,
                     status, tanggal, foto_final, fid))
            conn.commit()
            conn.close()
            if foto_baru and lama["foto_nikah"] and lama["foto_nikah"] != foto_baru:
                delete_upload(lama["foto_nikah"])
            return self.redirect(f"/admin/keluarga/{fid}", "Data keluarga disimpan.")
        except sqlite3.IntegrityError:
            conn.close()
            if foto_baru:
                delete_upload(foto_baru)
            return self.redirect(back, "NIK itu sudah terdaftar (duplikat).")

    def post_keluarga_foto_hapus(self, user, fid):
        conn = db()
        fam = conn.execute("SELECT foto_nikah FROM families WHERE id=?", (fid,)).fetchone()
        if not fam:
            conn.close()
            return self.not_found(user)
        conn.execute("UPDATE families SET foto_nikah=NULL WHERE id=?", (fid,))
        conn.commit()
        conn.close()
        delete_upload(fam["foto_nikah"])
        return self.redirect(f"/admin/keluarga/{fid}", "Foto pernikahan dihapus.")

    def post_keluarga_hapus(self, user, fid):
        conn = db()
        fam = conn.execute("SELECT * FROM families WHERE id=?", (fid,)).fetchone()
        if not fam:
            conn.close()
            return self.not_found(user)
        anak = conn.execute("SELECT foto FROM children WHERE family_id=?", (fid,)).fetchall()
        doks = conn.execute("SELECT stored_name FROM dokumen WHERE family_id=?",
                            (fid,)).fetchall()
        conn.execute("DELETE FROM dokumen WHERE family_id=?", (fid,))
        conn.execute("DELETE FROM children WHERE family_id=?", (fid,))
        conn.execute("DELETE FROM families WHERE id=?", (fid,))
        conn.commit()
        conn.close()
        delete_upload(fam["foto_nikah"])
        for a in anak:
            delete_upload(a["foto"])
        for d in doks:
            delete_upload(d["stored_name"])
        return self.redirect("/admin", "Data keluarga dihapus.")

    # -- admin: anak ----------------------------------------------------------
    def post_anak_tambah(self, user, fid, f, files):
        nama = (f.get("nama") or "").strip()
        if not nama:
            return self.redirect(f"/admin/keluarga/{fid}", "Nama anak wajib diisi.")
        foto, err = save_upload(files, "foto")
        if err:
            return self.redirect(f"/admin/keluarga/{fid}", err)
        conn = db()
        fam = conn.execute("SELECT id FROM families WHERE id=?", (fid,)).fetchone()
        if not fam:
            conn.close()
            if foto:
                delete_upload(foto)
            return self.not_found(user)
        urutan = conn.execute(
            "SELECT COALESCE(MAX(urutan), 0) + 1 n FROM children WHERE family_id=?",
            (fid,)).fetchone()["n"]
        conn.execute("INSERT INTO children (family_id, nama, foto, urutan) VALUES (?,?,?,?)",
                     (fid, nama, foto, urutan))
        conn.commit()
        conn.close()
        return self.redirect(f"/admin/keluarga/{fid}", f"Anak '{nama}' ditambahkan.")

    def admin_anak_form(self, user, cid, flash):
        conn = db()
        church = nama_gereja(conn)
        a = conn.execute(
            """SELECT c.*, f.kepala_nama FROM children c
               JOIN families f ON f.id=c.family_id WHERE c.id=?""", (cid,)).fetchone()
        conn.close()
        if not a:
            return self.not_found(user)
        foto_html = (f'<p>Foto saat ini:<br><img class="foto-anak" src="/foto/{esc(a["foto"])}" alt=""></p>'
                     if a["foto"] else "")
        body = f"""<h2>Ubah Anak — Keluarga {esc(a['kepala_nama'])}</h2>
        <form method="post" action="/admin/anak/{cid}/edit" enctype="multipart/form-data">
          <label>Nama Anak <input name="nama" required value="{esc(a['nama'])}"></label>
          {foto_html}
          <label>Foto Anak (kosongkan bila tidak diganti)
            <input name="foto" type="file" accept=".jpg,.jpeg,.png,.webp"></label>
          <label class="cek"><input type="checkbox" name="hapus_foto" value="1"> Hapus foto anak ini</label>
          <button type="submit">Simpan</button></form>
        <p><a href="/admin/keluarga/{a['family_id']}">Kembali ke keluarga</a></p>"""
        return self.send_html(layout(user, church, "Ubah Anak", body, flash))

    def post_anak_edit(self, user, cid, f, files):
        conn = db()
        a = conn.execute("SELECT * FROM children WHERE id=?", (cid,)).fetchone()
        if not a:
            conn.close()
            return self.not_found(user)
        fid = a["family_id"]
        nama = (f.get("nama") or "").strip()
        if not nama:
            conn.close()
            return self.redirect(f"/admin/anak/{cid}/edit", "Nama anak wajib diisi.")
        foto_baru, err = save_upload(files, "foto")
        if err:
            conn.close()
            return self.redirect(f"/admin/anak/{cid}/edit", err)
        foto_final = a["foto"]
        if f.get("hapus_foto") == "1":
            foto_final = None
        if foto_baru:
            foto_final = foto_baru
        conn.execute("UPDATE children SET nama=?, foto=? WHERE id=?", (nama, foto_final, cid))
        conn.commit()
        conn.close()
        if foto_final != a["foto"]:
            delete_upload(a["foto"])
        return self.redirect(f"/admin/keluarga/{fid}", "Data anak disimpan.")

    def post_anak_hapus(self, user, cid):
        conn = db()
        a = conn.execute("SELECT * FROM children WHERE id=?", (cid,)).fetchone()
        if not a:
            conn.close()
            return self.not_found(user)
        doks = conn.execute("SELECT stored_name FROM dokumen WHERE child_id=?",
                            (cid,)).fetchall()
        conn.execute("DELETE FROM dokumen WHERE child_id=?", (cid,))
        conn.execute("DELETE FROM children WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        delete_upload(a["foto"])
        for d in doks:
            delete_upload(d["stored_name"])
        return self.redirect(f"/admin/keluarga/{a['family_id']}", "Anak dihapus.")

    def post_anak_pindah(self, user, cid, arah):
        conn = db()
        a = conn.execute("SELECT * FROM children WHERE id=?", (cid,)).fetchone()
        if not a:
            conn.close()
            return self.not_found(user)
        sibs = conn.execute(
            "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id",
            (a["family_id"],)).fetchall()
        idx = [s["id"] for s in sibs].index(cid)
        j = idx + arah
        if 0 <= j < len(sibs):
            lain = sibs[j]
            conn.execute("UPDATE children SET urutan=? WHERE id=?", (lain["urutan"], cid))
            conn.execute("UPDATE children SET urutan=? WHERE id=?", (a["urutan"], lain["id"]))
            conn.commit()
        conn.close()
        return self.redirect(f"/admin/keluarga/{a['family_id']}", "Urutan anak diubah.")

    # -- admin: pengaturan ------------------------------------------------------
    def _seksi_gambar(self, judul, keterangan, aksi, berkas):
        """Satu seksi unggah gambar situs (banner / wallpaper) di Pengaturan."""
        if berkas:
            saat_ini = (f'<p>Gambar saat ini:<br>'
                        f'<img class="pratinjau-atur" src="/foto/{esc(berkas)}" alt="{esc(judul)}"></p>')
            hapus = (f"""<form method="post" action="{aksi}-hapus"
              onsubmit="return confirm('Hapus {esc(judul)} ini? Halaman publik kembali ke tampilan bawaan.')">
              <button type="submit" class="danger">Hapus {esc(judul)}</button></form>""")
        else:
            saat_ini = ('<p><small>Belum ada gambar — halaman publik memakai '
                        'tampilan bawaan (kepala &amp; latar polos).</small></p>')
            hapus = ""
        return f"""<section class="panel"><h3>{esc(judul)}</h3>
        <p><small>{keterangan}</small></p>
        {saat_ini}
        <form method="post" action="{aksi}" enctype="multipart/form-data">
          <label>Unggah / ganti gambar (JPG/PNG/WebP, maks 5 MB)
            <input name="gambar" type="file" accept=".jpg,.jpeg,.png,.webp" required></label>
          <button type="submit">Simpan {esc(judul)}</button></form>{hapus}</section>"""

    def admin_pengaturan(self, user, flash):
        conn = db()
        church = nama_gereja(conn)
        banner = get_setting(conn, "banner_foto", "")
        wallpaper = get_setting(conn, "wallpaper_foto", "")
        logo = get_setting(conn, "logo", "")
        warna = get_setting(conn, "warna_tema", "") or WARNA_TEMA_DEFAULT
        if not WARNA_HEX_RE.fullmatch(warna):
            warna = WARNA_TEMA_DEFAULT
        warna = warna.lower()
        conn.close()
        swatch_html = ""
        for nama_preset, hex_preset in WARNA_TEMA_PRESET:
            terpilih = ' terpilih' if hex_preset == warna else ""
            swatch_html += f"""<form method="post" action="/admin/pengaturan/warna" class="swatch-form">
              <input type="hidden" name="warna" value="{hex_preset}">
              <button type="submit" class="swatch{terpilih}"
                style="background:{hex_preset};color:{teks_di(hex_preset)}">{nama_preset}</button></form>"""
        seksi_warna = f"""<section class="panel"><h3>Warna Tema</h3>
        <p><small>Warna utama aplikasi: tombol, kepala halaman, dan menu.
        Warna gelap pendamping serta warna teks di atasnya dihitung
        otomatis agar tetap terbaca. Berlaku di semua halaman (admin,
        jemaat, masuk, dan halaman publik).</small></p>
        <div class="swatch-baris">{swatch_html}</div>
        <form method="post" action="/admin/pengaturan/warna">
          <label>Warna khusus <input type="color" name="warna" value="{esc(warna)}"></label>
          <button type="submit">Simpan Warna</button></form></section>"""
        seksi_logo = self._seksi_gambar(
            "Logo Gereja",
            "Logo tampil di halaman publik (di atas kartu pencarian), halaman "
            "masuk, dan kepala aplikasi. Gunakan gambar persegi, misalnya PNG "
            "berlatar transparan.",
            "/admin/pengaturan/logo", logo)
        seksi_banner = self._seksi_gambar(
            "Banner Halaman Publik",
            "Banner tampil membentang di bagian atas halaman publik (halaman pencarian jemaat).",
            "/admin/pengaturan/banner", banner)
        seksi_wallpaper = self._seksi_gambar(
            "Wallpaper Halaman Publik",
            "Wallpaper menjadi gambar latar (background) halaman publik. "
            "Pilih gambar yang tidak terlalu ramai agar teks tetap mudah dibaca.",
            "/admin/pengaturan/wallpaper", wallpaper)
        body = f"""<h2>Pengaturan</h2>
        <section class="panel"><h3>Nama Gereja</h3>
        <p><small>Nama ini tampil di kepala halaman dan judul halaman publik.</small></p>
        <form method="post" action="/admin/pengaturan">
          <label>Nama Gereja <input name="nama_gereja" required value="{esc(church)}"></label>
          <button type="submit">Simpan Nama Gereja</button></form></section>
        {seksi_warna}
        {seksi_logo}
        {seksi_banner}
        {seksi_wallpaper}
        <section class="panel"><h3>Backup &amp; Pulihkan</h3>
        <p><small>Unduh satu berkas ZIP berisi seluruh data &amp; foto, atau
        pulihkan data dari berkas backup.</small></p>
        <p><a href="/admin/backup"><button type="button">Buka Backup &amp; Pulihkan</button></a></p></section>
        <section class="panel"><h3>Ganti Password Admin</h3>
        <form method="post" action="/ganti-password">
          <label>Password baru <input name="password" type="password" required minlength="8"></label>
          <button type="submit">Ganti Password</button></form></section>"""
        return self.send_html(layout(user, church, "Pengaturan", body, flash,
                                     aktif="pengaturan"))

    def post_pengaturan(self, user, f):
        nama = (f.get("nama_gereja") or "").strip()
        if not nama:
            return self.redirect("/admin/pengaturan", "Nama gereja tidak boleh kosong.")
        conn = db()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('nama_gereja', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (nama,))
        conn.commit()
        conn.close()
        return self.redirect("/admin/pengaturan", "Nama gereja disimpan.")

    def post_warna_tema(self, user, f):
        """Simpan warna tema dari Pengaturan (preset maupun pemilih warna
        khusus). Hanya format #rrggbb yang diterima."""
        warna = (f.get("warna") or "").strip().lower()
        if not WARNA_HEX_RE.fullmatch(warna):
            return self.redirect("/admin/pengaturan",
                                 "Warna tidak dikenal — gunakan format #rrggbb.")
        conn = db()
        self._set_setting(conn, "warna_tema", warna)
        conn.commit()
        conn.close()
        return self.redirect("/admin/pengaturan", "Warna tema disimpan.")

    def _set_setting(self, conn, key, value):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def post_gambar_situs(self, user, key, files, label):
        """Unggah/ganti banner atau wallpaper. Berkas disimpan & disajikan
        persis seperti foto keluarga: nama token acak di folder uploads,
        diambil lewat route /foto/<nama>."""
        baru, err = save_upload(files, "gambar")
        if err:
            return self.redirect("/admin/pengaturan", err)
        if not baru:
            return self.redirect("/admin/pengaturan", f"Pilih berkas gambar {label} dulu.")
        conn = db()
        lama = get_setting(conn, key, "")
        self._set_setting(conn, key, baru)
        conn.commit()
        conn.close()
        if lama and lama != baru:
            delete_upload(lama)
        return self.redirect("/admin/pengaturan", f"{label} halaman publik disimpan.")

    def post_gambar_situs_hapus(self, user, key, label):
        conn = db()
        lama = get_setting(conn, key, "")
        self._set_setting(conn, key, "")
        conn.commit()
        conn.close()
        delete_upload(lama)
        return self.redirect("/admin/pengaturan", f"{label} dihapus.")

    def post_ganti_password(self, user, f):
        if len(f.get("password") or "") < 8:
            return self.redirect("/admin/pengaturan", "Password baru minimal 8 karakter.")
        ph, salt = hash_password(f["password"])
        conn = db()
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?",
                     (ph, salt, user["id"]))
        conn.commit()
        conn.close()
        return self.redirect("/admin", "Password berhasil diganti.")

    # -- admin: backup & pulihkan ---------------------------------------------
    def admin_backup(self, user, flash):
        church = self.church()
        body = """<h2>Backup &amp; Pulihkan</h2>
        <section class="panel"><h3>Unduh Backup</h3>
        <p><small>Satu berkas ZIP berisi SELURUH data aplikasi ini: database
        (semua keluarga, anak, dokumen, akun admin, dan pengaturan) serta
        semua berkas di folder unggahan (foto keluarga, dokumen, banner,
        wallpaper, dan logo). Simpan berkas ini di tempat yang aman —
        berkas inilah yang dipakai untuk memulihkan data bila suatu saat
        data di server hilang.</small></p>
        <p><a href="/admin/backup/unduh"><button type="button">Unduh Backup (.zip)</button></a></p></section>
        <section class="panel"><h3>Pulihkan dari Backup</h3>
        <div class="flash warn">PERINGATAN: memulihkan backup akan MENGGANTI
        SELURUH data saat ini (semua keluarga, anak, foto, banner, wallpaper,
        dan pengaturan) dengan isi berkas backup. Data yang ada sekarang akan
        hilang. Bila ragu, unduh backup terbaru dulu sebelum memulihkan.</div>
        <form method="post" action="/admin/backup/pulihkan" enctype="multipart/form-data"
          onsubmit="return confirm('Pulihkan backup ini? SELURUH data saat ini akan diganti dengan isi backup.')">
          <label>Berkas backup (.zip) <input name="berkas" type="file" accept=".zip" required></label>
          <button type="submit" class="danger">Pulihkan Backup</button></form></section>"""
        return self.send_html(layout(user, church, "Backup & Pulihkan", body, flash,
                                     aktif="backup"))

    def admin_backup_unduh(self, user):
        """Bangun ZIP backup saat itu juga: salinan database yang konsisten
        (lewat sqlite3 backup API), semua berkas di folder uploads di bawah
        uploads/, dan manifest.json penanda aplikasi."""
        tmpdir = tempfile.mkdtemp(prefix="jemaat-backup-")
        try:
            db_copy = os.path.join(tmpdir, "jemaat.db")
            src = sqlite3.connect(DB_PATH)
            try:
                dst = sqlite3.connect(db_copy)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(db_copy, "jemaat.db")
                if os.path.isdir(UPLOADS_DIR):
                    for name in sorted(os.listdir(UPLOADS_DIR)):
                        fpath = os.path.join(UPLOADS_DIR, name)
                        if os.path.isfile(fpath):
                            z.write(fpath, "uploads/" + name)
                manifest = {
                    "app": "data-jemaat-gereja",
                    "dibuat": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "versi": 1,
                }
                z.writestr("manifest.json", json.dumps(manifest, indent=2))
            data = buf.getvalue()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        fname = "backup-jemaat-" + datetime.date.today().isoformat() + ".zip"
        return self.send_bytes(data, "application/zip", fname)

    def post_backup_pulihkan(self, user, files):
        """Ganti database + isi folder uploads dengan isi berkas backup.
        Semua validasi dilakukan SEBELUM ada berkas yang disentuh; bila
        berkas ditolak, data saat ini tidak berubah sama sekali."""
        back = "/admin/backup"
        bukan = "Berkas ini bukan backup aplikasi data jemaat."
        if "berkas" not in files or not files["berkas"][0]:
            return self.redirect(back, "Pilih berkas backup (.zip) dulu.")
        _fname, data = files["berkas"]
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return self.redirect(back, "Berkas tidak valid — bukan file ZIP backup.")
        names = set(zf.namelist())
        if "manifest.json" not in names:
            return self.redirect(back, bukan)
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception:
            return self.redirect(back, bukan)
        if not isinstance(manifest, dict) or manifest.get("app") != "data-jemaat-gereja":
            return self.redirect(back, bukan)
        if "jemaat.db" not in names:
            return self.redirect(back, bukan)
        tmpdir = tempfile.mkdtemp(prefix="jemaat-pulih-")
        try:
            db_baru = os.path.join(tmpdir, "jemaat.db")
            try:
                with open(db_baru, "wb") as fh:
                    fh.write(zf.read("jemaat.db"))
                uploads_baru = os.path.join(tmpdir, "uploads")
                os.makedirs(uploads_baru, exist_ok=True)
                for name in zf.namelist():
                    if not name.startswith("uploads/"):
                        continue
                    base = os.path.basename(name)
                    if not base or not (FOTO_NAME_RE.fullmatch(base)
                                        or DOK_NAME_RE.fullmatch(base)):
                        continue  # lewati entri asing / upaya zip-slip
                    with open(os.path.join(uploads_baru, base), "wb") as fh:
                        fh.write(zf.read(name))
            except (zipfile.BadZipFile, RuntimeError, OSError):
                return self.redirect(back, "Berkas tidak valid — isi backup rusak.")
            # Pastikan database di dalam backup utuh & memang milik aplikasi ini.
            try:
                cek = sqlite3.connect(db_baru)
                tabel = {r[0] for r in cek.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                cek.close()
            except sqlite3.Error:
                return self.redirect(back, "Berkas tidak valid — database di dalam backup rusak.")
            if not {"users", "families", "children", "settings"} <= tabel:
                return self.redirect(back, "Berkas tidak valid — database di dalam backup tidak lengkap.")
            # --- Semua validasi lolos: titik tukar data. ---
            db_dir = os.path.dirname(DB_PATH) or "."
            os.makedirs(db_dir, exist_ok=True)
            if os.path.isfile(DB_PATH):
                # Salinan pengaman data SEBELUM dipulihkan, di samping DB aktif.
                shutil.copyfile(DB_PATH, os.path.join(db_dir, "jemaat-sebelum-pulih.db"))
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            for lama in os.listdir(UPLOADS_DIR):
                p = os.path.join(UPLOADS_DIR, lama)
                if os.path.isfile(p):
                    os.remove(p)
            for name in os.listdir(uploads_baru):
                shutil.copyfile(os.path.join(uploads_baru, name),
                                os.path.join(UPLOADS_DIR, name))
            shutil.copyfile(db_baru, DB_PATH)
            # Backup dari versi lama mungkin belum punya tabel/kolom baru:
            # jalankan skema + migrasi ringan pada database hasil pulihkan.
            conn = db()
            conn.executescript(SCHEMA)
            kolom = {r[1] for r in conn.execute("PRAGMA table_info(families)")}
            if "kata_sandi_hash" not in kolom:
                conn.execute("ALTER TABLE families ADD COLUMN kata_sandi_hash TEXT")
            if "kata_sandi_salt" not in kolom:
                conn.execute("ALTER TABLE families ADD COLUMN kata_sandi_salt TEXT")
            conn.commit()
            conn.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        # Database berganti (termasuk akun admin di dalamnya): semua sesi
        # login lama tidak berlaku lagi, admin masuk ulang dari /login.
        LOGIN_SESSIONS.clear()
        return self.redirect("/login", "Backup dipulihkan. Silakan masuk lagi.")


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Data Jemaat Gereja jalan di http://127.0.0.1:{PORT}  (DB: {DB_PATH}, uploads: {UPLOADS_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")


if __name__ == "__main__":
    main()
