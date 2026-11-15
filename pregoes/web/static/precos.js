/* Preços — os dois comportamentos próprios desta tela (o resto é do app.js):

   1. Busca em andamento: o aviso "a próxima leva de itens sai em ~N min".
      O app.js já consulta o progresso da tarefa e escreve `extra.espera_s`
      (segundos até a próxima consulta poder sair — o limite dos fornecedores)
      no span #p-espera-s; aqui só se observa esse span, sem pedido a mais.
      Mostra a partir de 15 s de espera: abaixo disso é o ritmo normal.

   2. Diálogo "Procurar de outro jeito": dizer o que está sendo esperado
      (consultar o site leva alguns segundos). Quem tem `data-espera="texto"`
      escreve o texto no `.p-estado` da SUA barra (`.p-busca`) ao ser
      acionado; a resposta do servidor troca esse elemento pela mensagem
      final. `{campo}` no texto vira o valor do campo do formulário. */
(function () {
  "use strict";

  /* ---------- 1. leva em espera ---------- */
  const ESPERA_MINIMA_S = 15;
  function pintarLeva() {
    const fonte = document.getElementById("p-espera-s");
    const aviso = document.getElementById("p-leva");
    if (!fonte || !aviso) return;
    const s = parseInt(fonte.textContent, 10) || 0;
    aviso.classList.toggle("oculto", !(s > ESPERA_MINIMA_S));
    const min = document.getElementById("p-leva-min");
    if (min) min.textContent = String(Math.max(1, Math.round(s / 60)));
  }
  function observarLeva() {
    const fonte = document.getElementById("p-espera-s");
    if (!fonte) return;
    new MutationObserver(pintarLeva).observe(fonte, { childList: true, characterData: true, subtree: true });
    pintarLeva();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observarLeva);
  else observarLeva();

  /* ---------- 2. o que se espera nas buscas manuais ---------- */
  function esperar(origem, campos) {
    const barra = origem.closest(".p-busca");
    const estado = barra && barra.querySelector(".p-estado");
    if (!estado || !origem.dataset.espera) return;
    const texto = origem.dataset.espera.replace(/\{(\w+)\}/g, function (_, nome) {
      const v = String((campos && campos.get(nome)) || "").trim();
      return nome === "fornecedor" ? v.charAt(0).toUpperCase() + v.slice(1) : v;
    });
    const linha = document.createElement("p");
    linha.className = "p-esperando";
    linha.textContent = texto;
    estado.replaceChildren(linha);
  }
  document.addEventListener("submit", function (e) {
    const form = e.target.closest && e.target.closest("form[data-espera]");
    if (!form) return;
    const campos = new FormData(form);
    if (!String(campos.get("termo") || "").trim()) return;   // o servidor pede o termo
    esperar(form, campos);
  }, true);
  document.addEventListener("click", function (e) {
    const botao = e.target.closest && e.target.closest("button[data-espera]");
    if (botao) esperar(botao, null);
  }, true);
})();
