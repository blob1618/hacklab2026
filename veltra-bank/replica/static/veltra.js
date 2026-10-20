/*
 * Variante local de prueba: el login usa /api/login y el paso 2 envía
 * {codigo} directamente a /api/verificar, sin challenge ni SHA-256.
 */
(function () {
  "use strict";

  function mostrarError(msg) {
    var el = document.getElementById("alert");
    if (el) {
      el.textContent = msg;
      el.style.display = "";
    }
  }

  function enviarJSON(url, cuerpo) {
    var opciones = { method: "GET", headers: {} };
    if (cuerpo !== undefined) {
      opciones.method = "POST";
      opciones.headers["Content-Type"] = "application/json";
      opciones.body = JSON.stringify(cuerpo);
    }
    return fetch(url, opciones).then(function (r) { return r.json(); });
  }

  // --- Paso 1: login ---
  var formLogin = document.getElementById("form-login");
  if (formLogin) {
    formLogin.addEventListener("submit", function (e) {
      e.preventDefault();
      var usuario = document.getElementById("txt-login").value.trim();
      var clave = document.getElementById("txt-password").value;
      enviarJSON("/api/login", { usuario: usuario, clave: clave })
        .then(function (data) {
          if (data.ok) { window.location = data.next; }
          else { mostrarError(data.error || "No se pudo iniciar sesion."); }
        })
        .catch(function () { mostrarError("Error de red."); });
    });
  }

  // --- Paso 2: 2FA ---
  var form2fa = document.getElementById("form-2fa");
  if (form2fa) {
    form2fa.addEventListener("submit", function (e) {
      e.preventDefault();
      var codigo = document.getElementById("txt-2fa").value.trim();
      enviarJSON("/api/verificar", { codigo: codigo })
        .then(function (res) {
          if (res.ok) { window.location = res.next; }
          else { mostrarError(res.error || "Codigo incorrecto."); }
        })
        .catch(function () { mostrarError("Error de red."); });
    });
  }
})();
