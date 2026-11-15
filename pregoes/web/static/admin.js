/* Pregões — Configurações: o Início e as mensagens de erro apontam para
   "/configuracoes#ia". A aba aberta é decidida no servidor (?aba=…), então um
   endereço com âncora é trocado pelo equivalente antes de a página aparecer. */
(function () {
  "use strict";
  var abas = ["almeida", "dantas", "ia", "backup", "sistema"];
  var pedida = (window.location.hash || "").replace("#", "");
  if (abas.indexOf(pedida) >= 0 && !/[?&]aba=/.test(window.location.search)) {
    window.location.replace(window.location.pathname + "?aba=" + pedida);
  }
})();
