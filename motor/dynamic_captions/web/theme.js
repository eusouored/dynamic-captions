/* theme.js — tema claro/escuro do painel a partir da cor do painel do Premiere (CEP).
   Põe ou tira a classe "light" no <html> e dispara um resize para a onda ser redesenhada
   com as cores novas (o main.js lê as cores das variáveis CSS). Sem o host (navegador), fica escuro. */
(function () {
  "use strict";
  function apply() {
    try {
      var env = JSON.parse(window.__adobe_cep__.getHostEnvironment());
      var c = env.appSkinInfo.panelBackgroundColor.color;
      var light = (0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue) / 255 > 0.5;
      var root = document.documentElement;
      if (root.classList.contains("light") !== light) {
        root.classList.toggle("light", light);
        window.dispatchEvent(new Event("resize"));
      }
    } catch (e) { /* fora do Premiere: mantém o tema escuro */ }
  }
  apply();
  try { window.__adobe_cep__.addEventListener("com.adobe.csxs.events.ThemeColorChanged", apply); } catch (e) {}
})();
