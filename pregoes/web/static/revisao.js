/* Revisão dos itens e mapeamento manual — comportamento destas duas telas.
   Usa só o que app.js publica em window.Pregoes (postar, aplicar, avisar).

   A grade da revisão salva POR DELEGAÇÃO: um único ouvinte na tabela, e cada
   célula alterada vira POST {i, n, <campo>: valor} — o mesmo formato do
   data-salvar de app.js, sem repetir o endereço em cada um dos milhares de
   campos de um edital grande. */
(function () {
  "use strict";
  const P = window.Pregoes;
  if (!P) return;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

  /* ===================== Revisão: a grade ===================== */
  const grade = $("#rev-grade");
  if (grade) iniciarGrade(grade);

  function iniciarGrade(grade) {
    const rolagem = grade.closest(".rev-rolagem");
    const campoFiltro = $("[data-filtrar]", grade.closest(".cartao"));
    const semResultado = $("#rev-sem-resultado");
    let filtro = "todos";

    const corpo = () => grade.tBodies[0];
    const linhas = () => $$("tr.rev-linha", grade);
    const duvidosa = (tr) => tr.classList.contains("duvidoso");
    const desmarcada = (tr) => !$("input[name=marcado]", tr).checked;
    const visivel = (tr) => !tr.classList.contains("oculto") && !tr.classList.contains("rev-fora");

    /* ---------- salvar uma célula ---------- */
    async function salvar(el) {
      const tr = el.closest("tr.rev-linha");
      if (!tr) return;
      const caixa = el.type === "checkbox";
      const dados = { i: Number(tr.dataset.i), n: Number(corpo().dataset.n) };
      if (caixa) {
        /* Mudar os itens marcados depois de buscar descarta a busca: pergunta antes. */
        const pergunta = grade.dataset.confirmarBusca;
        if (pergunta) {
          if (!window.confirm(pergunta)) { el.checked = !el.checked; return; }
          dados.confirmar = true;
        }
        dados.marcado = el.checked;
      } else {
        dados[el.name] = el.value;
      }
      try {
        const r = await P.postar(grade.dataset.celulas, dados);
        if (!caixa) {
          el.removeAttribute("aria-invalid");
          /* O servidor devolve o valor como ficou gravado ("1500,5" → "1.500,5").
             Só troca se o operador não voltou a digitar no campo. */
          if (typeof r.valor === "string" && document.activeElement !== el) el.value = r.valor;
          el.defaultValue = el.value;   /* Esc volta para o último valor SALVO; o filtro enxerga o texto novo */
          crescer([el]);
        }
        if (r.duvidoso === false && duvidosa(tr)) {
          tr.classList.remove("duvidoso");
          const selo = $(".rev-conferir", tr); if (selo) selo.remove();
        }
        P.aplicar(r, el);
        contar();
      } catch (e) {
        if (caixa) el.checked = !el.checked; else el.setAttribute("aria-invalid", "true");
        P.avisar(e.message, "erro");
      }
    }
    grade.addEventListener("change", (e) => {
      if (e.target.matches("input[name], textarea[name]")) salvar(e.target);
    });

    /* ---------- excluir linha ---------- */
    grade.addEventListener("click", async (e) => {
      const botao = e.target.closest(".rev-excluir");
      if (!botao) return;
      const tr = botao.closest("tr.rev-linha");
      const numero = $("input[name=item_numero]", tr).value.trim();
      const descricao = $("textarea", tr).value.trim();
      let pergunta = "Excluir " + (numero ? "o item " + numero : "esta linha") +
        (descricao ? " — “" + descricao.slice(0, 80) + (descricao.length > 80 ? "…" : "") + "”" : "") +
        "? Ele sai do edital e não vai para a planilha.";
      if (grade.dataset.confirmarBusca) pergunta += "\n\n" + grade.dataset.confirmarBusca.replace(/ Continuar\?$/, "");
      if (!window.confirm(pergunta)) return;
      botao.classList.add("ocupado");
      const dados = { i: Number(tr.dataset.i), n: Number(corpo().dataset.n) };
      if (grade.dataset.confirmarBusca) dados.confirmar = true;
      try { P.aplicar(await P.postar(grade.dataset.excluir, dados), botao); }
      catch (err) { P.avisar(err.message, "erro"); if (botao.isConnected) botao.classList.remove("ocupado"); }
    });

    /* ---------- teclado: Enter desce, Shift+Enter sobe, Esc desfaz ---------- */
    grade.addEventListener("keydown", (e) => {
      const el = e.target;
      if (!el.matches("input[type=text], textarea")) return;
      if (e.key === "Escape") {
        if (el.value !== el.defaultValue) { el.value = el.defaultValue; el.removeAttribute("aria-invalid"); crescer([el]); }
        return;
      }
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();               /* descrição é uma linha só: Enter não quebra, navega */
      let tr = el.closest("tr");
      do { tr = e.shiftKey ? tr.previousElementSibling : tr.nextElementSibling; } while (tr && !visivel(tr));
      const alvo = tr && $('[name="' + el.name + '"]', tr);
      if (alvo) { alvo.focus(); alvo.select(); } else el.blur();   /* última linha: só salva */
    });

    /* ---------- descrição que cresce com o texto ---------- */
    const cresceSozinho = window.CSS && CSS.supports && CSS.supports("field-sizing", "content");
    function crescer(campos) {
      if (cresceSozinho) return;
      const areas = campos.filter((c) => c.tagName === "TEXTAREA" && c.offsetParent !== null);
      /* Em dois tempos (zera tudo, mede tudo, aplica tudo): medir e aplicar
         campo a campo refaz o desenho da tabela inteira a cada linha. */
      areas.forEach((a) => { a.style.height = "auto"; });
      const alturas = areas.map((a) => a.scrollHeight + 2);
      areas.forEach((a, k) => { a.style.height = alturas[k] + "px"; });
    }
    grade.addEventListener("input", (e) => { if (e.target.tagName === "TEXTAREA") crescer([e.target]); });

    /* ---------- filtros ---------- */
    function contar() {
      const todas = linhas();
      const q = { todos: todas.length, duvidosos: todas.filter(duvidosa).length, desmarcados: todas.filter(desmarcada).length };
      $$("[data-rev-filtro]").forEach((b) => { $(".q", b).textContent = q[b.dataset.revFiltro]; });
    }
    function conferirVazio() {
      if (semResultado) semResultado.classList.toggle("oculto", linhas().some(visivel));
      crescer($$("textarea", grade));    /* linha que estava escondida não tinha altura medida */
    }
    function filtrar(novo) {
      if (novo) filtro = novo;
      $$("[data-rev-filtro]").forEach((b) => {
        const ativo = b.dataset.revFiltro === filtro;
        b.classList.toggle("ativo", ativo);
        b.setAttribute("aria-pressed", ativo ? "true" : "false");
      });
      linhas().forEach((tr) => {
        const fora = (filtro === "duvidosos" && !duvidosa(tr)) || (filtro === "desmarcados" && !desmarcada(tr));
        tr.classList.toggle("rev-fora", fora);
      });
      conferirVazio();
    }
    function limparFiltros() {
      if (campoFiltro && campoFiltro.value) { campoFiltro.value = ""; campoFiltro.dispatchEvent(new Event("input", { bubbles: true })); }
      filtrar("todos");
    }
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-rev-filtro]");
      if (b) { filtrar(b.dataset.revFiltro); if (rolagem) rolagem.scrollTop = 0; return; }
      if (e.target.closest("[data-rev-limpar]")) { limparFiltros(); if (campoFiltro) campoFiltro.focus(); }
    });
    /* O filtro de texto é o data-filtrar de app.js (classe .oculto); aqui só
       confere, depois dele, se sobrou alguma linha. */
    if (campoFiltro) campoFiltro.addEventListener("input", () => setTimeout(conferirVazio, 0));

    /* ---------- o corpo da tabela foi trocado (marcar todos, incluir, excluir) ---------- */
    new MutationObserver(() => {
      const nova = $("tr[data-nova]", grade);
      if (nova) {
        limparFiltros();
      } else {
        if (campoFiltro && campoFiltro.value) campoFiltro.dispatchEvent(new Event("input", { bubbles: true }));
        filtrar();
      }
      contar();
      crescer($$("textarea", grade));
      if (nova) {
        nova.scrollIntoView({ block: "nearest" });
        const d = $("textarea", nova); if (d) d.focus();
      }
    }).observe(grade, { childList: true });

    contar();
    crescer($$("textarea", grade));
  }

  /* ===================== Mapeamento manual ===================== */
  /* Passo 1: escolher a tabela recarrega a tela com ?tabela=N. */
  const seletor = $("[data-map-tabela]");
  if (seletor) seletor.addEventListener("change", () => {
    window.location.href = seletor.dataset.mapTabela + encodeURIComponent(seletor.value);
  });

  /* Passos 2 e 3: avisa campo repetido e o que falta, a cada escolha. O
     servidor valida de novo ao aplicar — aqui é só para o operador ver na hora. */
  const form = $("[data-map-form]");
  if (form) {
    const selects = $$("select[data-map-coluna]", form);
    const faltando = $("#map-faltando", form);
    const botao = $("button[type=submit]", form);
    const obrigatorios = (form.dataset.obrigatorios || "").split(",").filter(Boolean);
    const rotulo = (campo) => { const o = $('option[value="' + campo + '"]', selects[0]); return o ? o.textContent.trim() : campo; };
    const conferir = () => {
      const usados = new Set();
      selects.forEach((s) => {
        const aviso = $(".map-repetido", s.closest("label"));
        const repetido = s.value !== "ignorar" && usados.has(s.value);
        if (aviso) aviso.textContent = repetido ? "“" + rotulo(s.value) + "” já está em outra coluna." : "";
        if (s.value !== "ignorar") usados.add(s.value);
      });
      const falta = obrigatorios.filter((c) => !usados.has(c)).map(rotulo);
      if (faltando) {
        faltando.classList.toggle("oculto", !falta.length);
        const b = $("b", faltando); if (b) b.textContent = falta.join(", ");
      }
      if (botao) botao.disabled = falta.length > 0;
    };
    form.addEventListener("change", (e) => { if (e.target.matches("select[data-map-coluna]")) conferir(); });
    conferir();
  }
})();
