// Data Jemaat Gereja — bantuan kecil di sisi browser.
// 1) Input NIK: hanya angka, maksimal 16 digit.
// 2) Pratinjau foto yang dipilih sebelum diunggah (halaman admin).
// 3) Tombol mata pada SEMUA kolom password: pengguna bisa menampilkan
//    isi ketikannya untuk memeriksa salah ketik, lalu menyembunyikan lagi.
(function () {
  document.querySelectorAll('input[name="nik"], input[name="kepala_nik"], input[name="pasangan_nik"]')
    .forEach(function (el) {
      el.addEventListener('input', function () {
        el.value = el.value.replace(/\D/g, '').slice(0, 16);
      });
    });

  document.querySelectorAll('input[type="file"][accept]').forEach(function (el) {
    el.addEventListener('change', function () {
      var old = el.parentNode.querySelector('.pratinjau-foto');
      if (old) old.remove();
      var file = el.files && el.files[0];
      if (!file || !/^image\//.test(file.type)) return;
      var img = document.createElement('img');
      img.className = 'pratinjau-foto';
      img.alt = 'Pratinjau foto';
      img.src = URL.createObjectURL(file);
      el.parentNode.appendChild(img);
    });
  });
})();

// Tombol mata password: dipasang otomatis pada setiap input
// type="password" di halaman mana pun (masuk admin, Login Jemaat,
// formulir keluarga, ganti password jemaat & admin). Input dibungkus
// .pw-wrap agar tombol bisa diposisikan di dalam kolom tanpa mengubah
// tata letak formulir; tombol type="button" jadi tidak pernah
// mengirim formulir.
(function () {
  var IKON_MATA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z">'
    + '</path><circle cx="12" cy="12" r="3"></circle></svg>';
  var IKON_MATA_CORET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8'
    + 'a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8'
    + 'a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>'
    + '<line x1="1" y1="1" x2="23" y2="23"></line></svg>';

  function pasangMata(input) {
    if (input.dataset.pwMata) return;
    input.dataset.pwMata = '1';
    var bungkus = document.createElement('span');
    bungkus.className = 'pw-wrap';
    input.parentNode.insertBefore(bungkus, input);
    bungkus.appendChild(input);
    var tombol = document.createElement('button');
    tombol.type = 'button';
    tombol.className = 'pw-toggle';
    tombol.innerHTML = IKON_MATA;
    tombol.setAttribute('aria-label', 'Tampilkan password');
    tombol.setAttribute('aria-pressed', 'false');
    tombol.addEventListener('click', function () {
      var tampil = input.type === 'password';
      input.type = tampil ? 'text' : 'password';
      tombol.innerHTML = tampil ? IKON_MATA_CORET : IKON_MATA;
      tombol.setAttribute('aria-label',
        tampil ? 'Sembunyikan password' : 'Tampilkan password');
      tombol.setAttribute('aria-pressed', tampil ? 'true' : 'false');
      input.focus();
    });
    bungkus.appendChild(tombol);
  }

  function pasangSemua() {
    document.querySelectorAll('input[type="password"]').forEach(pasangMata);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', pasangSemua);
  } else {
    pasangSemua();
  }
})();
