/* Pregões — comportamento da interface web. JavaScript puro, sem biblioteca:
   o servidor devolve HTML pronto; aqui só há (1) envio de formulários sem
   recarregar, (2) troca de pedaços da página, (3) acompanhamento de tarefas
   demoradas e (4) miudezas de uso (avisos, diálogos, tema, atalhos). */
(function () {
  "use strict";
  const RAIZ = document.documentElement.dataset.raiz || "";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const url = (c) => (c.startsWith("/") ? RAIZ + c : c);

  /* ---------- avisos flutuantes ---------- */
  function avisar(texto, tipo) {
    let caixa = $("#flutuantes");
    if (!caixa) { caixa = document.createElement("div"); caixa.id = "flutuantes"; document.body.appendChild(caixa); }
    const el = document.createElement("div");
    el.className = "flutuante" + (tipo ? " " + tipo : "");
    el.setAttribute("role", "status");
    el.textContent = texto;
    caixa.appendChild(el);
    setTimeout(() => el.remove(), tipo === "erro" ? 9000 : 4500);
  }

  /* ---------- chamadas ao servidor ---------- */
  async function chamar(caminho, opcoes) {
    let resp;
    try {
      resp = await fetch(url(caminho), Object.assign({ headers: { "X-Requested-With": "pregoes" } }, opcoes || {}));
    } catch (e) {
      throw new Error("Sem conexão com o servidor. Confira a internet e tente de novo.");
    }
    const tipo = resp.headers.get("content-type") || "";
    const corpo = tipo.includes("json") ? await resp.json() : await resp.text();
    if (!resp.ok) {
      const msg = (corpo && corpo.erro) || (typeof corpo === "string" && corpo.length < 300 && corpo) ||
        "O servidor não conseguiu atender agora (" + resp.status + ").";
      throw new Error(msg);
    }
    return corpo;
  }
  const postar = (caminho, dados) => chamar(caminho, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Requested-With": "pregoes" },
    body: JSON.stringify(dados || {}),
  });

  /* Aplica a resposta padrão do servidor:
     {ok, aviso, erro, ir, recarregar, html: {"#seletor": "<…>"}, tarefa} */
  function aplicar(r, origem) {
    if (!r || typeof r !== "object") return;
    if (r.html) for (const [sel, html] of Object.entries(r.html)) {
      const alvo = $(sel);
      if (alvo) { alvo.outerHTML = html; }
    }
    if (r.aviso) avisar(r.aviso, r.tipo || "ok");
    if (r.erro) avisar(r.erro, "erro");
    if (r.fechar_modal) { const d = origem && origem.closest("dialog"); if (d) d.close(); }
    if (r.tarefa) acompanhar(r.tarefa);
    if (r.ir) { window.location.href = url(r.ir); return; }
    if (r.recarregar) window.location.reload();
    ligar(document);
  }

  /* ---------- formulários e botões assíncronos ---------- */
  function dadosDoForm(form) {
    const d = {};
    new FormData(form).forEach((v, k) => {
      if (v instanceof File) return;
      if (k in d) { if (!Array.isArray(d[k])) d[k] = [d[k]]; d[k].push(v); } else d[k] = v;
    });
    $$("input[type=checkbox][name]", form).forEach((c) => {
      if (!c.hasAttribute("value")) d[c.name] = c.checked;
    });
    return d;
  }

  async function enviar(form, botao) {
    if (form.dataset.confirmar && !window.confirm(form.dataset.confirmar)) return;
    const b = botao || $("button[type=submit], button:not([type])", form);
    if (b) b.classList.add("ocupado");
    try {
      let r;
      if (form.enctype === "multipart/form-data") {
        r = await chamar(form.getAttribute("action"), { method: "POST", body: new FormData(form) });
      } else {
        const dados = dadosDoForm(form);
        if (botao && botao.name) dados[botao.name] = botao.value;
        r = await postar(form.getAttribute("action"), dados);
      }
      aplicar(r, form);
    } catch (e) {
      avisar(e.message, "erro");
    } finally {
      if (b && b.isConnected) b.classList.remove("ocupado");
    }
  }

  async function acionar(botao) {
    if (botao.dataset.confirmar && !window.confirm(botao.dataset.confirmar)) return;
    botao.classList.add("ocupado");
    try {
      let dados = {};
      if (botao.dataset.dados) dados = JSON.parse(botao.dataset.dados);
      aplicar(await postar(botao.dataset.acao, dados), botao);
    } catch (e) {
      avisar(e.message, "erro");
    } finally {
      if (botao.isConnected) botao.classList.remove("ocupado");
    }
  }

  /* ---------- tarefas demoradas ---------- */
  const acompanhando = new Set();
  function tempo(s) {
    if (s == null) return "";
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), seg = s % 60;
    if (h) return h + "h " + String(m).padStart(2, "0") + "min";
    return String(m).padStart(2, "0") + ":" + String(seg).padStart(2, "0");
  }
  function restante(s) {
    if (s == null) return "";
    if (s >= 5400) return "faltam ~" + (s / 3600).toFixed(1).replace(".", ",") + " h";
    return "faltam ~" + Math.max(1, Math.round(s / 60)) + " min";
  }
  function pintarTarefa(t) {
    $$('[data-tarefa="' + t.id + '"]').forEach((el) => {
      const pct = Math.round(t.fracao * 100);
      const set = (sel, v) => { const x = $(sel, el); if (x) x.textContent = v; };
      set(".pct", pct + "%");
      set(".titulo-t", t.titulo);
      set(".item-atual", t.detalhe || "");
      set(".tempo", [tempo(t.decorrido_s), t.estado === "rodando" ? restante(t.restante_s) : ""].filter(Boolean).join(" · "));
      const barra = $(".barra", el);
      if (barra) {
        $("i", barra).style.width = pct + "%";
        barra.classList.toggle("pausada", !!(t.extra && t.extra.pausa_s > 0));
      }
      const pausa = $(".pausa-t", el);
      if (pausa) pausa.classList.toggle("oculto", !(t.extra && t.extra.pausa_s > 0));
      $$("[data-extra]", el).forEach((x) => {
        const v = t.extra ? t.extra[x.dataset.extra] : null;
        if (v != null) x.textContent = v;
      });
    });
  }
  async function acompanhar(id) {
    if (!id || acompanhando.has(id)) return;
    acompanhando.add(id);
    let falhas = 0;
    while (true) {
      let t;
      try { t = await chamar("/api/tarefas/" + id); falhas = 0; }
      catch (e) {
        if (++falhas >= 6) { avisar("Perdi o contato com o servidor. A tarefa continua lá — recarregue a página para acompanhar.", "erro"); break; }
        await new Promise((r) => setTimeout(r, 3000)); continue;
      }
      pintarTarefa(t);
      if (t.estado !== "rodando") {
        acompanhando.delete(id);
        const el = $('[data-tarefa="' + id + '"]');
        const destino = el && el.dataset.aoTerminar;
        if (t.estado === "erro") avisar(t.erro || "A tarefa falhou.", "erro");
        if (destino) window.location.href = url(destino); else window.location.reload();
        return;
      }
      await new Promise((r) => setTimeout(r, t.extra && t.extra.pausa_s > 0 ? 4000 : 1500));
    }
    acompanhando.delete(id);
  }

  /* ---------- diálogos ---------- */
  async function abrirModal(caminho) {
    let d = $("#modal");
    if (!d) { d = document.createElement("dialog"); d.id = "modal"; d.className = "modal"; document.body.appendChild(d); }
    d.innerHTML = '<div class="corpo vazio">Carregando…</div>';
    if (!d.open) d.showModal();
    try {
      d.innerHTML = await chamar(caminho);
      d.className = "modal" + (d.firstElementChild && d.firstElementChild.dataset.largo ? " largo" : "");
      ligar(d);
      const foco = $("[autofocus]", d); if (foco) foco.focus();
    } catch (e) { d.close(); avisar(e.message, "erro"); }
  }

  /* ---------- envio de arquivo por arrastar ---------- */
  function ligarSolta(zona) {
    const entrada = $("input[type=file]", zona);
    const form = zona.closest("form");
    if (!entrada || !form) return;
    zona.addEventListener("click", (e) => { if (e.target !== entrada) entrada.click(); });
    ["dragenter", "dragover"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.add("sobre"); }));
    ["dragleave", "drop"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.remove("sobre"); }));
    zona.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { entrada.files = e.dataTransfer.files; entrada.dispatchEvent(new Event("change", { bubbles: true })); } });
    entrada.addEventListener("change", () => {
      if (!entrada.files.length) return;
      const nome = $(".nome-arquivo", zona); if (nome) nome.textContent = Array.from(entrada.files).map((f) => f.name).join(", ");
      if (form.dataset.enviarAoEscolher != null) enviar(form);
    });
  }

  /* ---------- ligação dos comportamentos (idempotente) ---------- */
  function ligar(raiz) {
    $$("form[data-async]", raiz).forEach((f) => {
      if (f.dataset.ligado) return; f.dataset.ligado = "1";
      f.addEventListener("submit", (e) => { e.preventDefault(); enviar(f, e.submitter); });
    });
    $$("[data-acao]", raiz).forEach((b) => {
      if (b.dataset.ligado) return; b.dataset.ligado = "1";
      b.addEventListener("click", (e) => { e.preventDefault(); acionar(b); });
    });
    $$("[data-modal]", raiz).forEach((b) => {
      if (b.dataset.ligado) return; b.dataset.ligado = "1";
      b.addEventListener("click", (e) => { e.preventDefault(); abrirModal(b.dataset.modal); });
    });
    $$("[data-fechar]", raiz).forEach((b) => {
      if (b.dataset.ligado) return; b.dataset.ligado = "1";
      b.addEventListener("click", (e) => { e.preventDefault(); const d = b.closest("dialog"); if (d) d.close(); });
    });
    /* campo que salva sozinho ao mudar: data-salvar="/caminho" (envia {nome: valor}) */
    $$("[data-salvar]", raiz).forEach((c) => {
      if (c.dataset.ligado) return; c.dataset.ligado = "1";
      c.addEventListener("change", async () => {
        const dados = c.dataset.dados ? JSON.parse(c.dataset.dados) : {};
        dados[c.name] = c.type === "checkbox" ? c.checked : c.value;
        try { aplicar(await postar(c.dataset.salvar, dados), c); }
        catch (e) { avisar(e.message, "erro"); }
      });
    });
    $$(".solta", raiz).forEach((z) => { if (!z.dataset.ligado) { z.dataset.ligado = "1"; ligarSolta(z); } });
    $$("[data-tarefa]", raiz).forEach((el) => acompanhar(el.dataset.tarefa));
    $$("[data-filtrar]", raiz).forEach((c) => {
      if (c.dataset.ligado) return; c.dataset.ligado = "1";
      c.addEventListener("input", () => {
        const termo = c.value.trim().toLowerCase();
        $$(c.dataset.filtrar).forEach((linha) => {
          linha.classList.toggle("oculto", !!termo && !linha.textContent.toLowerCase().includes(termo));
        });
      });
    });
  }

  /* ---------- tema ---------- */
  function aplicarTema(t) {
    if (t) document.documentElement.dataset.tema = t; else delete document.documentElement.dataset.tema;
  }
  try { aplicarTema(localStorage.getItem("pregoes-tema")); } catch (e) { /* sem armazenamento: segue o sistema */ }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tema-alternar]");
    if (!b) return;
    const escuroAgora = document.documentElement.dataset.tema
      ? document.documentElement.dataset.tema === "escuro"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const novo = escuroAgora ? "claro" : "escuro";
    aplicarTema(novo);
    try { localStorage.setItem("pregoes-tema", novo); } catch (err) { /* idem */ }
  });

  /* ---------- atalhos ---------- */
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      const busca = $("[data-atalho-busca]");
      if (busca) { e.preventDefault(); busca.focus(); busca.select(); }
    }
  });

  window.Pregoes = { avisar, chamar, postar, aplicar, acompanhar, abrirModal, ligar };
  document.addEventListener("DOMContentLoaded", () => ligar(document));
})();
