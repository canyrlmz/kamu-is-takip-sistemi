// Rol seçilince uygun olmayan izinleri devre dışı bırakır ve önerilen izinleri işaretler.
// Yalnızca kolaylıktır: asıl doğrulama sunucuda (rol tavanı, users.manage yasağı) yapılır.
(function () {
  var holder = document.getElementById("perm-config");
  var roleSelect = document.getElementById("role");
  if (!holder || !roleSelect) { return; }
  var config = JSON.parse(holder.getAttribute("data-config"));
  var boxes = Array.prototype.slice.call(document.querySelectorAll(".perm-checkbox"));

  function apply(applyPreset) {
    var role = roleSelect.value;
    var ceiling = config.ceilings[role] || [];
    var preset = config.presets[role] || [];
    boxes.forEach(function (box) {
      var allowed = ceiling.indexOf(box.value) !== -1;
      box.disabled = !allowed;
      if (!allowed) { box.checked = false; }
      else if (applyPreset) { box.checked = preset.indexOf(box.value) !== -1; }
    });
  }

  roleSelect.addEventListener("change", function () { apply(true); });
  apply(false);
})();
