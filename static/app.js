// Data Jemaat Gereja — bantuan kecil di sisi browser.
// 1) Input NIK: hanya angka, maksimal 16 digit.
// 2) Pratinjau foto yang dipilih sebelum diunggah (halaman admin).
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
