#!/usr/bin/env python3
"""
Data Jemaat Gereja — Direktori Keluarga Jemaat
Stack: Python 3 standard library SAJA (http.server + sqlite3).
Tanpa framework web dan tanpa library eksternal sama sekali.

Dua sisi aplikasi:
1. PUBLIK (tanpa login): jemaat mengecek data keluarganya.
   - Cari dengan NIK (tepat 16 digit, milik kepala keluarga ATAU pasangan)
     -> tampil KARTU KELUARGA LENGKAP: foto nikah, status perkawinan,
        tanggal menikah, nama kepala keluarga, nama pasangan, dan daftar
        anak (Anak 1, Anak 2, ...) beserta foto tiap anak bila ada.
   - Cari dengan NAMA (sebagian nama) -> tampil DAFTAR MINIMAL saja:
        nama kepala keluarga, nama pasangan, status perkawinan.
        TANPA foto, TANPA anak, TANPA tanggal menikah, TANPA NIK.
        Pengaman ini SENGAJA: detail lengkap + foto hanya bisa dibuka
        dengan NIK yang tepat.
2. ADMIN (satu akun admin, wajib login): mengelola data keluarga & anak,
   mengunggah foto, mengatur nama gereja, serta banner & wallpaper
   halaman publik.

Jalankan:  python3 app.py            (default http://127.0.0.1:8000)
           PORT=8080 python3 app.py
           JEMAAT_DB=/path/jemaat.db JEMAAT_UPLOADS=/path/uploads python3 app.py
"""
import hashlib
import html
import os
import re
import secrets
import sqlite3
import urllib.parse
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
FOTO_NAME_RE = re.compile(r"[a-f0-9]{24}\.(?:jpg|jpeg|png|webp)")
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
    foto_nikah TEXT                         -- nama berkas foto di folder uploads, boleh kosong
);
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    foto TEXT,                              -- nama berkas foto anak, boleh kosong
    urutan INTEGER NOT NULL DEFAULT 0       -- urutan tampil (Anak 1, 2, 3, ...)
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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
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
# ---------------------------------------------------------------------------
LOGIN_SESSIONS = {}  # token -> user_id


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
    if name and FOTO_NAME_RE.fullmatch(name):
        try:
            os.remove(os.path.join(UPLOADS_DIR, name))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# HTML layout
# ---------------------------------------------------------------------------
def layout(user, church, title, body, flash="", bg=None):
    # bg: nama berkas wallpaper (dari Pengaturan) untuk latar halaman publik.
    # Bila kosong, halaman memakai latar polos bawaan dari style.css.
    bg_style = ""
    if bg:
        bg_style = (f"<style>body{{background-image:url('/foto/{esc(bg)}');"
                    "background-size:cover;background-position:center;"
                    "background-attachment:fixed;}</style>")
    nav = ""
    if user:
        nav = ('<a href="/admin">Dashboard</a><a href="/admin/keluarga/baru">Tambah Keluarga</a>'
               '<a href="/admin/pengaturan">Pengaturan</a><a href="/">Lihat Halaman Publik</a>'
               '<a href="/logout">Keluar</a>')
    else:
        nav = '<a href="/">Cari Data Keluarga</a><a href="/login">Masuk Admin</a>'
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    user_html = (f'<div class="userbar">Masuk sebagai {esc(user["nama"])}</div>') if user else ""
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - {esc(church)}</title>
<link rel="stylesheet" href="/static/style.css">{bg_style}</head>
<body><header><h1>{esc(church)}</h1>{user_html}<nav>{nav}</nav></header>
<main>{flash_html}{body}</main>
<footer><small>Direktori Keluarga Jemaat &bull; Detail lengkap hanya tampil dengan NIK yang tepat</small></footer>
<script src="/static/app.js"></script>
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

    def redirect(self, path, flash="", set_cookie=None):
        headers = []
        if set_cookie:
            headers.append(("Set-Cookie", set_cookie))
        if flash:
            path += ("&" if "?" in path else "?") + "msg=" + urllib.parse.quote(flash)
        headers.append(("Location", path))
        self.send_html("", status=303, extra_headers=headers)

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("jemaat_session")
        if not morsel:
            return None
        uid = LOGIN_SESSIONS.get(morsel.value)
        if not uid:
            return None
        conn = db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
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

    def parse_multipart(self):
        """Parser multipart/form-data minimal (standard library saja).
        Kembalikan (fields, files); files: nama_field -> (nama_berkas, bytes)."""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=(.+)", ctype)
        if not m:
            return {}, {}
        boundary = m.group(1).strip().strip('"')
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_BODY_BYTES:
            raise ValueError("Ukuran unggahan terlalu besar (maksimal sekitar 5 MB per foto).")
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
            body = """<h2>Masuk Admin</h2>
            <form method="post" action="/login">
              <label>Username <input name="username" required autocomplete="username"></label>
              <label>Password <input name="password" type="password" required autocomplete="current-password"></label>
              <button type="submit">Masuk</button></form>
            <p><small>Akun uji lokal ada di README proyek. Jangan pakai akun awal di produksi.</small></p>"""
            return self.send_html(layout(None, self.church(), "Masuk", body, flash))
        if path == "/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get("jemaat_session")
            if morsel:
                LOGIN_SESSIONS.pop(morsel.value, None)
            return self.redirect("/", "Sampai jumpa.",
                                 set_cookie="jemaat_session=; Max-Age=0; Path=/")

        user = self.current_user()
        if not user:
            return self.redirect("/login", "Silakan masuk dulu.")
        if path == "/admin":
            return self.admin_dashboard(user, qs, flash)
        if path == "/admin/keluarga/baru":
            return self.admin_keluarga_form(user, None, flash)
        if path == "/admin/pengaturan":
            return self.admin_pengaturan(user, flash)
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
        user = self.current_user()
        if not user:
            return self.redirect("/login", "Silakan masuk dulu.")
        f, files = self.read_form()
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
        Tidak ada directory listing; nama di luar pola token ditolak."""
        name = urllib.parse.unquote(raw_name)
        if not FOTO_NAME_RE.fullmatch(name):
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
            LOGIN_SESSIONS[token] = row["id"]
            return self.redirect("/admin", f"Selamat datang, {row['nama']}.",
                                 set_cookie=f"jemaat_session={token}; HttpOnly; Path=/; SameSite=Lax")
        return self.redirect("/login", "Username atau password salah.")

    # -----------------------------------------------------------------------
    # PUBLIK: pencarian jemaat
    # -----------------------------------------------------------------------
    def home(self, qs, flash):
        conn = db()
        church = nama_gereja(conn)
        hasil = ""
        mode = (qs.get("cari") or [""])[0]
        if mode == "nik":
            nik = re.sub(r"\D", "", (qs.get("nik") or [""])[0])
            if not valid_nik(nik):
                hasil = ('<div class="flash warn">NIK harus tepat 16 digit angka. '
                         'Periksa lagi NIK pada KTP kepala keluarga atau pasangan.</div>')
            else:
                fam = conn.execute(
                    "SELECT * FROM families WHERE kepala_nik=? OR pasangan_nik=?",
                    (nik, nik)).fetchone()
                if fam:
                    anak = conn.execute(
                        "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id",
                        (fam["id"],)).fetchall()
                    hasil = self.kartu_keluarga_lengkap(fam, anak)
                else:
                    hasil = ('<div class="flash warn">Data keluarga dengan NIK itu tidak '
                             'ditemukan. Pastikan NIK sudah benar (16 digit, milik kepala '
                             'keluarga atau pasangan), atau hubungi admin/pengurus gereja.</div>')
        elif mode == "nama":
            q = (qs.get("nama") or [""])[0].strip()
            if len(q) < 2:
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
                        cari dengan <b>NIK</b> kepala keluarga / pasangan pada formulir
                        NIK di atas.</small></li>""" for r in rows)
                    hasil = (f'<section class="panel"><h3>Hasil pencarian nama</h3>'
                             f'<ul>{items}</ul></section>')
                else:
                    hasil = ('<div class="flash warn">Tidak ada keluarga dengan nama itu. '
                             'Coba ejaan lain, atau cari langsung dengan NIK.</div>')
        banner = get_setting(conn, "banner_foto", "")
        wallpaper = get_setting(conn, "wallpaper_foto", "")
        conn.close()
        banner_html = (f'<img class="banner" src="/foto/{esc(banner)}" '
                       f'alt="Banner {esc(church)}">' if banner else "")
        body = f"""{banner_html}<h2>Cari Data Keluarga Jemaat</h2>
        <p>Selamat datang di direktori keluarga {esc(church)}. Silakan cek data
        keluarga Anda dengan salah satu cara berikut.</p>
        <section class="panel"><h3>Cari dengan NIK (detail lengkap)</h3>
        <p><small>Masukkan NIK kepala keluarga atau NIK pasangan (16 digit).
        Bila NIK tepat, kartu keluarga lengkap akan tampil: foto pernikahan,
        status &amp; tanggal menikah, dan daftar anak.</small></p>
        <form method="get" action="/">
          <input type="hidden" name="cari" value="nik">
          <label>NIK (16 digit) <input name="nik" inputmode="numeric" autocomplete="off"
            maxlength="16" placeholder="mis. 3201xxxxxxxxxxxx" required></label>
          <button type="submit">Cari dengan NIK</button></form></section>
        <section class="panel"><h3>Cari dengan Nama (daftar singkat)</h3>
        <p><small>Pencarian nama hanya menampilkan daftar singkat (nama kepala
        keluarga, pasangan, status). Foto, anak, tanggal menikah, dan NIK
        <b>tidak</b> ditampilkan — detail lengkap hanya bisa dibuka dengan NIK.</small></p>
        <form method="get" action="/">
          <input type="hidden" name="cari" value="nama">
          <label>Nama kepala keluarga / pasangan <input name="nama" autocomplete="off"
            placeholder="mis. Budi" required></label>
          <button type="submit">Cari dengan Nama</button></form></section>
        {hasil}"""
        return self.send_html(layout(None, church, "Cari Data Keluarga", body, flash,
                                     bg=wallpaper or None))

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
    # ADMIN
    # -----------------------------------------------------------------------
    def admin_dashboard(self, user, qs, flash):
        conn = db()
        church = nama_gereja(conn)
        q = (qs.get("q") or [""])[0].strip()
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """SELECT f.*, (SELECT COUNT(*) FROM children c WHERE c.family_id=f.id) jml_anak
                   FROM families f
                   WHERE f.kepala_nama LIKE ? OR f.pasangan_nama LIKE ?
                      OR f.kepala_nik LIKE ? OR f.pasangan_nik LIKE ?
                   ORDER BY f.kepala_nama LIMIT 100""",
                (like, like, like, like)).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.*, (SELECT COUNT(*) FROM children c WHERE c.family_id=f.id) jml_anak
                   FROM families f ORDER BY f.kepala_nama LIMIT 100""").fetchall()
        total_keluarga = conn.execute("SELECT COUNT(*) c FROM families").fetchone()["c"]
        total_anak = conn.execute("SELECT COUNT(*) c FROM children").fetchone()["c"]
        conn.close()
        items = "".join(
            f"""<li><a href="/admin/keluarga/{r['id']}"><b>{esc(r['kepala_nama'])}</b></a>
            &mdash; pasangan: {esc(r['pasangan_nama'] or '-')}
            &mdash; {esc(r['status'])} &mdash; {r['jml_anak']} anak
            <br><small>NIK kepala: <code>{esc(r['kepala_nik'])}</code></small></li>"""
            for r in rows) or "<li>Belum ada data keluarga.</li>"
        warn = ""
        conn = db()
        me = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        conn.close()
        if me and verify_password("admin123", me["password_hash"], me["salt"]):
            warn = ('<div class="flash warn">PERINGATAN: akun admin ini masih memakai '
                    'password awal lokal. Segera ganti lewat menu Pengaturan '
                    'sebelum dipakai beneran.</div>')
        body = f"""<h2>Dashboard Admin</h2>{warn}
        <div class="grid">
          <div class="card"><b>{total_keluarga}</b><span>Keluarga</span></div>
          <div class="card"><b>{total_anak}</b><span>Anak</span></div>
        </div>
        <p><a href="/admin/keluarga/baru"><button type="button">+ Tambah Keluarga</button></a></p>
        <form method="get" action="/admin">
          <label>Cari keluarga (nama / NIK) <input name="q" value="{esc(q)}" autocomplete="off"></label>
          <button type="submit">Cari</button></form>
        <h3>Daftar Keluarga</h3><ul>{items}</ul>"""
        return self.send_html(layout(user, church, "Dashboard Admin", body, flash))

    def admin_keluarga_form(self, user, fid, flash):
        conn = db()
        church = nama_gereja(conn)
        fam = None
        anak = []
        if fid is not None:
            fam = conn.execute("SELECT * FROM families WHERE id=?", (fid,)).fetchone()
            if not fam:
                conn.close()
                return self.not_found(user)
            anak = conn.execute(
                "SELECT * FROM children WHERE family_id=? ORDER BY urutan, id", (fid,)).fetchall()
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
          {foto_html}
          <label>Foto Pernikahan (JPG/PNG/WebP, maks 5 MB; kosongkan bila tidak diganti)
            <input name="foto_nikah" type="file" accept=".jpg,.jpeg,.png,.webp"></label>
          <button type="submit">Simpan Keluarga</button></form>"""
        if fam:
            if fam["foto_nikah"]:
                body += f"""<form method="post" action="/admin/keluarga/{fid}/foto-hapus"
                  onsubmit="return confirm('Hapus foto pernikahan keluarga ini?')">
                  <button type="submit" class="danger">Hapus Foto Pernikahan</button></form>"""
            rows = ""
            for i, a in enumerate(anak):
                img = (f'<img class="foto-anak" src="/foto/{esc(a["foto"])}" alt="">'
                       if a["foto"] else "")
                rows += f"""<li><span class="anak-label">Anak {i + 1}</span> {img} {esc(a['nama'])}
                  &mdash; <a href="/admin/anak/{a['id']}/edit">Ubah</a>
                  <form method="post" action="/admin/anak/{a['id']}/naik" class="inline">
                    <button type="submit">Naik</button></form>
                  <form method="post" action="/admin/anak/{a['id']}/turun" class="inline">
                    <button type="submit">Turun</button></form>
                  <form method="post" action="/admin/anak/{a['id']}/hapus" class="inline"
                    onsubmit="return confirm('Hapus anak ini?')">
                    <button type="submit" class="danger">Hapus</button></form></li>"""
            body += f"""<section class="panel"><h3>Anak-anak</h3>
            <ul class="anak-list">{rows or '<li>Belum ada anak.</li>'}</ul>
            <h4>Tambah Anak</h4>
            <form method="post" action="/admin/keluarga/{fid}/anak" enctype="multipart/form-data">
              <label>Nama Anak <input name="nama" required></label>
              <label>Foto Anak (boleh kosong) <input name="foto" type="file"
                accept=".jpg,.jpeg,.png,.webp"></label>
              <button type="submit">Tambah Anak</button></form></section>
            <form method="post" action="/admin/keluarga/{fid}/hapus"
              onsubmit="return confirm('Hapus SELURUH data keluarga ini beserta anak-anaknya?')">
              <button type="submit" class="danger">Hapus Keluarga Ini</button></form>"""
        return self.send_html(layout(user, church, judul, body, flash))

    def post_keluarga_simpan(self, user, fid, f, files):
        back = f"/admin/keluarga/{fid}" if fid else "/admin/keluarga/baru"
        kepala_nama = (f.get("kepala_nama") or "").strip()
        kepala_nik = re.sub(r"\D", "", f.get("kepala_nik") or "")
        pasangan_nama = (f.get("pasangan_nama") or "").strip()
        pasangan_nik = re.sub(r"\D", "", f.get("pasangan_nik") or "")
        status = f.get("status") or "Menikah"
        tanggal = (f.get("tanggal_menikah") or "").strip() or None
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
                       pasangan_nik, status, tanggal_menikah, foto_nikah)
                       VALUES (?,?,?,?,?,?,?)""",
                    (kepala_nama, kepala_nik, pasangan_nama, pasangan_nik or None,
                     status, tanggal, foto_baru))
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
        conn.execute("DELETE FROM children WHERE family_id=?", (fid,))
        conn.execute("DELETE FROM families WHERE id=?", (fid,))
        conn.commit()
        conn.close()
        delete_upload(fam["foto_nikah"])
        for a in anak:
            delete_upload(a["foto"])
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
        conn.execute("DELETE FROM children WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        delete_upload(a["foto"])
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
        conn.close()
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
        {seksi_banner}
        {seksi_wallpaper}
        <section class="panel"><h3>Ganti Password Admin</h3>
        <form method="post" action="/ganti-password">
          <label>Password baru <input name="password" type="password" required minlength="8"></label>
          <button type="submit">Ganti Password</button></form></section>"""
        return self.send_html(layout(user, church, "Pengaturan", body, flash))

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
        return self.redirect("/admin/pengaturan",
                             f"{label} dihapus. Halaman publik kembali ke tampilan bawaan.")

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
