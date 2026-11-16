/* Dynamic Captions — painel 0.2.0 ("documento").
 * Fluxo: sequência ativa → Legendar (áudio exportado pelo Premiere em segundo plano) →
 * um documento só: quadro real com a legenda arrastável, estilos como amostras, ajustes e a fala.
 * Aplicar/Atualizar fica sempre no rodapé. */
(function () {
  "use strict";
  var host = window.DCHost;
  var WAIT_USE = "Clique no painel para iniciar o Dynamic Captions.";
  var $app = document.getElementById("app");
  var player = document.getElementById("player");

  var STAGES = [
    ["export", "Lendo o áudio da sequência"],
    ["vad", "Encontrando trechos com fala"],
    ["asr", "Reconhecendo as palavras"],
    ["align", "Ajustando o tempo de cada palavra"],
    ["verify", "Conferindo com um segundo modelo"],
    ["speakers", "Separando quem fala"],
    ["quality", "Checando a qualidade"]
  ];
  var STYLE_NAMES = { classico: "Clássico", karaoke_social: "Karaokê", caixa: "Caixa" };
  // Aberturas. O terceiro item marca as antigas, que só aparecem em projetos que já as usam.
  var MOTIONS = [["none","Sem abertura"],["fade","Surgir"],["pop","Pop"],["slide","Subir"],
    ["bounce","Salto"],["zoom","Zoom"],["typewriter","Digitação"],
    ["letter_fade","Letra a letra"],["curve_up","Curva para cima"],
    ["blur_ascend","Desfoque ascendente"],["blur_rise","Fade para cima"],["fade_down","Fade para baixo"],
    ["slide_left","Deslizar da esquerda"],["slide_right","Deslizar da direita"],
    ["tracking","Tracking"],["colors","Com cores"],["from_right","Da direita"],["random","Aleatória"],
    ["custom","Personalizada"],
    ["blur","Desfoque",true],["rotate","Inclinação",true]];
  var MIN_MOTION_MS = 100;  // 0,10 s: mais rápido que isso a abertura não chega a aparecer no vídeo
  var PLACE_ONLY = ["position", "offset_x_pct", "offset_y_pct", "margin_pct"];
  // "Personalizada" começa com os números da entrada que estava escolhida, para ajustar a partir dela.
  var E0 = { entry_unit: "word", entry_stagger_pct: 45, entry_rise_pct: 0, entry_blur_px: 0, entry_scale: 1, entry_rotate_deg: 0, entry_fade: true, entry_shift_pct: 0, entry_order: "forward", entry_tint: false };
  function entryFrom(o) { return Object.assign({}, E0, o); }
  var ENTRY_START = {
    fade_down: entryFrom({ entry_rise_pct: -3.5, entry_blur_px: 14 }),
    slide_left: entryFrom({ entry_stagger_pct: 50, entry_shift_pct: -5, entry_blur_px: 12 }),
    slide_right: entryFrom({ entry_stagger_pct: 50, entry_shift_pct: 5, entry_blur_px: 12 }),
    from_right: entryFrom({ entry_stagger_pct: 50, entry_rise_pct: 3.5, entry_blur_px: 10, entry_order: "reverse" }),
    letter_fade: entryFrom({ entry_unit: "letter", entry_stagger_pct: 15, entry_rise_pct: 1, entry_blur_px: 8 }),
    tracking: entryFrom({ entry_unit: "letter", entry_stagger_pct: 15, entry_shift_pct: 3, entry_blur_px: 8 }),
    colors: entryFrom({ entry_unit: "letter", entry_stagger_pct: 20, entry_blur_px: 5, entry_tint: true }),
    random: entryFrom({ entry_unit: "letter", entry_stagger_pct: 10, entry_blur_px: 8, entry_order: "random" }),
    blur_rise: { entry_unit: "word", entry_stagger_pct: 45, entry_rise_pct: 3.5, entry_blur_px: 14, entry_scale: 1, entry_rotate_deg: 0, entry_fade: true },
    curve_up: { entry_unit: "letter", entry_stagger_pct: 67, entry_rise_pct: -1.5, entry_blur_px: 0, entry_scale: 0.1, entry_rotate_deg: 0, entry_fade: false },
    blur_ascend: entryFrom({ entry_unit: "word", entry_stagger_pct: 27, entry_rise_pct: 7, entry_blur_px: 10 }),
    fade: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 0, entry_blur_px: 0, entry_scale: 1, entry_rotate_deg: 0, entry_fade: true },
    pop: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 0, entry_blur_px: 0, entry_scale: 0.7, entry_rotate_deg: 0, entry_fade: true },
    zoom: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 0, entry_blur_px: 0, entry_scale: 1.7, entry_rotate_deg: 0, entry_fade: true },
    slide: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 4, entry_blur_px: 0, entry_scale: 1, entry_rotate_deg: 0, entry_fade: true },
    bounce: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 6, entry_blur_px: 0, entry_scale: 1, entry_rotate_deg: 0, entry_fade: true },
    blur: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 0, entry_blur_px: 12, entry_scale: 1, entry_rotate_deg: 0, entry_fade: false },
    rotate: { entry_unit: "phrase", entry_stagger_pct: 0, entry_rise_pct: 0, entry_blur_px: 0, entry_scale: 0.92, entry_rotate_deg: -8, entry_fade: false }
  };
  var STYLE_DESCRIPTIONS = {
    classico:"Frase com entrada suave", karaoke_social:"Cor acumulada e pop", caixa:"Texto sobre fundo",
    palavra_ativa:"Só a palavra falada muda de cor", karaoke_preenchido:"Cor preenche as palavras",
    palavra_pop:"Uma palavra grande por vez", palavra_salto:"Palavras com impulso e retorno",
    palavra_caixa:"Caixa acompanha cada palavra", revelar:"Frase aparece conforme a fala",
    digitacao:"Letras aparecem no tempo da voz", neon:"Brilho com contorno nítido",
    cinema:"Discreto, com letras espaçadas", manchete:"Faixa no topo com deslizamento",
    zoom:"Entrada ampliada até o tamanho final", desfoque:"Do desfocado para o nítido",
    rotacao:"Texto inclinado se estabiliza",
    tipografia_2:"Palavra principal maior, duas linhas", tipografia_3:"Contexto, palavra principal e complemento",
    editorial:"Itálico editorial", marca_texto:"Fundo acompanha a palavra falada",
    palavra_pulso:"Frase fixa; só a palavra falada pulsa",
    desfoque_ascendente:"Palavras sobem desfocadas, em escada", curva:"Letras nascem uma a uma, em arco"
  };
  // Biblioteca 0.1.8: categorias vindas do catálogo (category) mais coleções locais do painel.
  var WORD_DEFAULTS = { font_scale: 1, font_family: null, bold: null, italic: null, text_color: null, break_before: false };
  var PRESET_FILE_SCHEMA = "dynamic-captions.estilos";
  var PRESET_FILE_VERSION = 1;
  var ENGINE_VERSION = "0.3.1";  // o painel troca um motor de outra versão pelo desta
  var HEX = /^#[0-9A-Fa-f]{6}$/;
  // Fonte e cores escolhidas em Ajustes valem para todas as amostras e sobrevivem à troca de estilo.
  // Onde e de que tamanho a legenda fica: escolha do vídeo, não do visual.
  var PLACE_KEYS = ["font_size_pct", "position", "offset_x_pct", "offset_y_pct", "margin_pct"];
  var LOOK_KEYS = ["font_family", "bold", "italic", "uppercase", "text_color", "highlight_color", "outline_color", "marker_color", "box_color"];
  // Campos da hierarquia (até 0.2.11): arquivos antigos ainda os trazem; são descartados sem aviso.
  var LEGACY_STYLE_KEYS = ["composition", "emphasis_scale", "secondary_scale", "line_gap_pct"];
  // Limites conferidos antes de enviar ao motor (o motor valida de novo).
  var STYLE_RULES = {
    font_family: ["string"], font_size_pct: ["number", 0.5, 30], bold: ["boolean"], italic: ["boolean"],
    spacing_px: ["number", 0, 20], glow_px: ["number", 0, 30], uppercase: ["boolean"],
    text_color: ["hex"], highlight_color: ["hex"], outline_color: ["hex"], outline_px: ["number", 0, 40],
    shadow_px: ["number", 0, 40], box: ["boolean"], box_color: ["hex"], box_opacity: ["number", 0, 1],
    position: ["enum", "bottom", "middle", "top"], margin_pct: ["number", 0, 45],
    text_mode: ["enum", "phrase", "word", "progressive"], karaoke: ["enum", "none", "fill", "color", "active"],
    animation_in: ["enum", "none", "fade", "pop", "slide", "bounce", "zoom", "blur", "rotate", "typewriter", "blur_rise", "curve_up", "custom", "blur_ascend",
      "letter_fade", "fade_down", "slide_left", "slide_right", "tracking", "colors", "from_right", "random"],
    animation_ms: ["number", 0, 2000],
    marker: ["enum", "none", "active"], marker_color: ["hex"], marker_opacity: ["number", 0, 1],
    marker_padding_px: ["number", 0, 30], marker_radius_px: ["number", 0, 40], active_scale: ["number", 1, 1.35], max_lines: ["number", 1, 2],
    offset_x_pct: ["number", -50, 50], offset_y_pct: ["number", -50, 50],
    wobble: ["boolean"], wobble_ms: ["number", 60, 600], wobble_pct: ["number", 0.3, 4], arch_pct: ["number", 0, 6],
    entry_unit: ["enum", "phrase", "word", "letter"], entry_stagger_pct: ["number", 0, 100], entry_rise_pct: ["number", -15, 15],
    entry_blur_px: ["number", 0, 40], entry_scale: ["number", 0.1, 2.5], entry_rotate_deg: ["number", -90, 90], entry_fade: ["boolean"],
    entry_shift_pct: ["number", -15, 15], entry_order: ["enum", "forward", "reverse", "random"], entry_tint: ["boolean"]
  };

  var S = {
    phase: "connecting", status: "Conectando…", conn: null, seq: null, scope: "seq", dictionary: "",
    busy: null, screen: "inicio", job: null, cancelRequested: false, error: null,
    pid: null, project: null, transcript: null, review: [], sel: null, draft: "", flash: "",
    style: null, fonts: [], blocks: [], mode: "animated", applyResult: null, adv: false, safe: true,
    frameBg: null, karaokeTick: 0, toast: null,
    catalog: [], savedStyles: [], presetName: "", savingStyle: false, speakerCount: "auto",
    animCard: null, layouts: {}, wordPanel: false, importReport: null,
    saveState: null, saveError: ""
  };
  function readList(key, max) {
    try { var v = JSON.parse(localStorage.getItem(key) || "[]"); return Array.isArray(v) ? v.slice(0, max) : []; } catch (e) { return []; }
  }
  function writeList(key, list) { try { localStorage.setItem(key, JSON.stringify(list)); return true; } catch (e) { return false; } }
  S.hiddenPresets = readList("dc:hiddenPresets", 60).filter(function (x) { return typeof x === "string"; });
  S.savedStyles = readList("dc:savedStyles", 50).filter(function (x) {
    return x && typeof x.name === "string" && typeof x.preset === "string" && x.overrides && typeof x.overrides === "object";
  });
  S.savedStyles.forEach(function (x) { LEGACY_STYLE_KEYS.forEach(function (k) { delete x.overrides[k]; }); });

  // ------------------------------------------------------------------ utilidades
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fps() { return S.seq ? S.seq.fps_num / S.seq.fps_den : 30000 / 1001; }
  /* Timecode como o Premiere mostra: quadros contados na taxa real, agrupados pela taxa nominal. */
  function tc(us) {
    var f = Math.round(us * fps() / 1e6), n = Math.round(fps());
    var drop = !!(S.seq ? S.seq.drop_frame : S.project && S.project.sequence && S.project.sequence.drop_frame) && (n === 30 || n === 60);
    if (drop) { // mesmo algoritmo do Premiere (e de domain/timecode.py)
      var d = n === 30 ? 2 : 4, per10 = n * 600 - d * 9, per1 = n * 60 - d;
      var tens = Math.floor(f / per10), rem = f % per10;
      f += rem > d ? d * 9 * tens + d * Math.floor((rem - d) / per1) : d * 9 * tens;
    }
    var fr = f % n, s = Math.floor(f / n), m = Math.floor(s / 60), h = Math.floor(m / 60);
    function p(x) { return (x < 10 ? "0" : "") + x; }
    var sep = drop ? ";" : ":";
    return p(h) + sep + p(m % 60) + sep + p(s % 60) + sep + p(fr);
  }
  function offset() { return (S.project && S.project.range && S.project.range.start_us) || 0; }
  function speaker(id) { return (S.transcript && S.transcript.speakers || []).find(function (s) { return s.id === id; }); }
  function activeSpeakers() {
    if (!S.transcript) return [];
    var used = {}; S.transcript.words.forEach(function (w) { used[w.speaker_id] = true; });
    return S.transcript.speakers.filter(function (sp) { return used[sp.id]; });
  }
  function catalogEntry(id) { return S.catalog.find(function (p) { return p.id === id; }) || null; }
  function spName(sp) { return sp ? (sp.display_name || sp.label) : "—"; }

  async function api(method, path, body) {
    var r = await fetch(S.conn.base + path, {
      method: method,
      headers: { "X-DC-Token": S.conn.token, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    var data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      var d = data && data.detail;
      var err = new Error(typeof d === "string" ? d : (d && d.message) || ("erro " + r.status));
      err.status = r.status; err.detail = d;
      throw err;
    }
    return data;
  }

  function mediaUrl(path, params) {
    var q = new URLSearchParams(params); q.set("token", S.conn.token);
    return S.conn.base + path + "?" + q.toString();
  }

  function toast(msg, kind) { S.toast = { msg: msg, kind: kind || "good" }; render(); clearTimeout(toast.t); toast.t = setTimeout(function () { S.toast = null; render(); }, 3500); }

  // ------------------------------------------------------------------ conexão
  async function connect() {
    S.phase = "connecting"; render();
    try {
      S.conn = await host.connect(function (msg) { S.status = msg; render(); }, ENGINE_VERSION);
      await api("GET", "/v1/projects");
      S.phase = "ready";
      S.banner = S.conn.versionMismatch ? { title: "Versões diferentes", text: "O motor instalado é a versão " + S.conn.versionMismatch + " e o painel é a " + ENGINE_VERSION + ". Reinstale o Dynamic Captions para atualizar os dois juntos." } : null;
      watchEngine();
      // Com o Premiere abrindo, o painel só conversa com ele depois que o editor usa o painel.
      var wait = host.waitForUse && host.waitForUse();
      if (wait) { S.phase = "connecting"; S.status = WAIT_USE; render(); await wait; S.status = "Lendo a sequência no Premiere…"; render(); S.phase = "ready"; }
      var side = Promise.all([
        api("GET", "/v1/system/fonts").then(function (r) { S.fonts = r.fonts || []; }, function () { S.fonts = []; }),
        api("GET", "/v1/style-catalog").then(function (r) {
          S.catalog = r.presets || [];
          S.catalog.forEach(function (p) { STYLE_NAMES[p.id] = p.name; });
        }, function () { S.catalog = []; })
      ]);
      await refreshSequence();
      await side;
    } catch (e) {
      S.phase = e.message === "NEEDS_SETUP" ? "setup" : e.message === "NEEDS_TOKEN" ? "token" : "motorOff";
      S.error = e.message === "ENGINE_TIMEOUT" ? "O motor demorou mais de 30 s para iniciar. Se o computador estiver ocupado, espere um pouco e tente de novo." : e.message;
    }
    render();
  }

  /* Os modelos carregam em segundo plano assim que o motor sobe; o topo mostra quando ficam prontos. */
  async function watchEngine() {
    for (var i = 0; i < 400; i++) {
      try { S.engine = await api("GET", "/v1/engine/status"); } catch (e) { S.engine = null; return; }
      var chip = document.querySelector(".top .eng");
      if (chip) chip.outerHTML = statusChip();
      if (!S.engine || S.engine.models !== "loading") return;
      await new Promise(function (r) { setTimeout(r, 1500); });
    }
  }

  async function refreshSequence(fresh) {
    if (!host.canPremiere) { S.seq = null; return; }
    try {
      var seq = await host.getSequence();
      var changed = !S.seq || !seq || S.seq.guid !== seq.guid;
      S.seq = seq;
      if (seq && seq.in_us == null && S.scope === "inout") S.scope = "seq";
      // Rotina do editor: marcou In/Out, é esse trecho que vai legendar (a não ser que tenha escolhido outro).
      if (seq && seq.in_us != null && !S.scopeChosen) S.scope = "inout";
      if (changed && !fresh) await restoreLast();
    } catch (e) { S.seq = null; S.seqError = e.message; return; }
    S.seqError = null;
  }

  async function restoreLast() {
    clearProject();
    if (!S.seq) return;
    var pid = null;
    try { pid = localStorage.getItem("dc:last:" + S.seq.guid); } catch (e) { pid = null; }
    if (!pid) return;
    try { await loadProject(pid); S.screen = "doc"; } catch (e) { clearProject(); }
  }

  function clearProject() {
    clearTimeout(styleTimer); styleGeneration++; S.savingStyle = false;
    S.pid = null; S.project = null; S.transcript = null; S.review = []; S.sel = null; S.blocks = []; S.layouts = {}; S.wordPanel = false;
    S.style = null; S.applyResult = null; S.frameBg = null; S.screen = "inicio";
    S.saveState = null; S.saveError = ""; S.wordPanel = false;
  }

  // ------------------------------------------------------------------ legendar
  async function legendar() {
    if (S.busy) return;
    S.busy = "legendar"; S.error = null; S.cancelRequested = false; S.backPid = null;
    clearProject();
    S.screen = "proc"; S.job = { stage: "export", progress: 0 }; render();
    try {
      await refreshSequence();
      var seq = S.seq;
      if (!seq) throw new Error(S.seqError || "Nenhuma sequência ativa na Timeline.");
      var useInOut = S.scope === "inout" && seq.in_us != null;
      var start = useInOut ? seq.in_us : 0, end = useInOut ? seq.out_us : (seq.content_end_us || seq.end_us);
      var project = await api("POST", "/v1/projects", {
        name: seq.name + (useInOut ? " (In/Out)" : ""),
        sequence: { name: seq.name, fps_num: seq.fps_num, fps_den: seq.fps_den, width: seq.width, height: seq.height, drop_frame: !!seq.drop_frame },
        range_start_us: start, range_end_us: end, sequence_guid: seq.guid
      });
      S.pid = project.id;
      var target = await api("POST", "/v1/projects/" + S.pid + "/audio-target");
      var presets = (await host.findAudioPresets()).concat(target.presets || []);
      if (!presets.length) throw new Error("Não encontrei o preset de áudio WAV do Premiere nesta máquina.");
      if (S.cancelRequested) throw new Error("CANCELADO");
      var exp = await host.exportAudio(target.path, presets[0], useInOut);
      if (S.cancelRequested) throw new Error("CANCELADO");
      var dictionary = S.dictionary.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
      var job = await api("POST", "/v1/jobs", { kind: "transcribe", project_id: S.pid, audio_path: exp.path,
        options: { dictionary: dictionary, num_speakers: S.speakerCount === "auto" ? null : Number(S.speakerCount) } });
      S.job = { id: job.job_id, stage: "vad", progress: 0.1 };
      render();
      var final = await pollJob(job.job_id, function (j) { S.job.stage = j.stage || S.job.stage; S.job.progress = j.progress; render(); });
      if (final.state === "cancelled") throw new Error("CANCELADO");
      if (final.state !== "done") throw new Error(final.error || "a transcrição falhou");
      if ((final.result.warnings || []).indexOf("nenhuma fala detectada") >= 0) { S.screen = "semFala"; return; }
      try { localStorage.setItem("dc:last:" + seq.guid, S.pid); } catch (e) { /* sem armazenamento */ }
      await loadProject(S.pid);
      S.screen = "doc";
      S.warnings = final.result.warnings || [];
      // 0.2.0: a segunda análise de vozes com contexto não roda mais sozinha (custava outra
      // exportação de áudio e outra diarização); fica no botão "Reanalisar vozes" da revisão.
      grabFrame();
      toast("Transcrição pronta: " + S.transcript.words.length + " palavras, " + activeSpeakers().length + " falante(s).");
    } catch (e) {
      if (e.message === "CANCELADO") { S.screen = "cancel"; }
      else { S.screen = "erro"; S.error = e.message; }
      S.pid = S.screen === "cancel" ? null : S.pid;
    } finally {
      S.busy = null; S.job = null; render();
    }
  }

  async function pollJob(id, onUpdate) {
    for (;;) {
      var j = await api("GET", "/v1/jobs/" + id);
      if (onUpdate) onUpdate(j);
      if (["done", "failed", "cancelled"].indexOf(j.state) >= 0) return j;
      await new Promise(function (r) { setTimeout(r, 300); });
    }
  }

  async function cancel() {
    S.cancelRequested = true;
    if (S.job && S.job.id) { try { await api("DELETE", "/v1/jobs/" + S.job.id); } catch (e) { /* já terminou */ } }
    render();
  }

  // ------------------------------------------------------------------ projeto e revisão
  async function loadProject(pid) {
    S.pid = pid;
    var res = await Promise.all([
      api("GET", "/v1/projects/" + pid),
      api("GET", "/v1/projects/" + pid + "/transcript"),
      api("GET", "/v1/projects/" + pid + "/review"),
      api("GET", "/v1/projects/" + pid + "/style")
    ]);
    S.project = res[0]; S.transcript = res[1]; S.review = res[2].items; S.style = res[3];
    S.warnings = S.project.warnings || [];
    S.speakerCount = S.project.speaker_count_requested ? String(S.project.speaker_count_requested) : "auto";
    await loadBlocks();
    S.frameUrl = null; S.frameT = null;
    grabFrame();
  }

  /* Quadro real da sequência no instante da legenda: fundo do monitor e das amostras.
     O Premiere exporta uma vez por instante; a prévia exata reaproveita o mesmo PNG. */
  var frameJob = null;
  function grabFrame(force) {
    if (!host.canPremiere || !S.pid || !S.project) return Promise.resolve(null);
    var t = previewTimeUs();
    if (!force && S.frameT === t && S.frameBg) return Promise.resolve(S.frameBg);
    if (frameJob && frameJob.t === t && !force) return frameJob.p;
    var pid = S.pid;
    var p = (async function () {
      try {
        var target = await api("POST", "/v1/projects/" + pid + "/audio-target");
        var png = target.path.replace(/\.wav$/i, "_quadro.png");
        await host.exportFrame(offset() + t, png, S.project.sequence_guid, previousManifest());
        if (S.pid !== pid) return null;
        S.frameBg = png; S.frameT = t;
        S.frameUrl = mediaUrl("/v1/projects/" + pid + "/frame.jpg", { w: 960 }) + "&v=" + Date.now();
        render();
        return png;
      } catch (e) { return null; } finally { frameJob = null; }
    })();
    frameJob = { t: t, p: p };
    return p;
  }

  async function reloadAfterEdit() {
    var res = await Promise.all([
      api("GET", "/v1/projects/" + S.pid + "/transcript"),
      api("GET", "/v1/projects/" + S.pid + "/review"),
      api("GET", "/v1/projects/" + S.pid)
    ]);
    S.transcript = res[0]; S.review = res[1].items; S.project = res[2];
    await loadBlocks();
  }

  async function loadBlocks() {
    try {
      var ce = S.seq && S.seq.guid === (S.project && S.project.sequence_guid) && S.seq.content_end_us;
      var res = await api("GET", "/v1/projects/" + S.pid + "/captions?preset=auto&style=projeto" + (ce ? "&content_end_us=" + ce : ""));
      S.blocks = res.blocks || []; S.layouts = res.layouts || {};
    } catch (e) { S.blocks = []; S.layouts = {}; }
  }

  var opsChain = Promise.resolve();
  function ops(list, okMsg) {
    if (S.busy && S.busy !== "ops") {
      toast(S.busy === "apply" ? "Espere terminar de aplicar para editar." : "Espere terminar o que está em andamento para editar.", "err");
      return Promise.resolve();
    }
    S.busy = "ops";
    opsChain = opsChain.then(function () { return runOps(list, okMsg); });
    return opsChain;
  }
  async function runOps(list, okMsg) {
    S.busy = "ops";
    try {
      await api("POST", "/v1/projects/" + S.pid + "/ops", { base_rev: S.transcript.rev, ops: list });
      await reloadAfterEdit();
      S.flash = okMsg || "";
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.reason === "revision") {
        await reloadAfterEdit();
        S.flash = "A transcrição mudou em outra janela; recarreguei. Tente de novo.";
      } else { S.flash = "Não foi possível: " + e.message; }
    } finally { S.busy = null; render(); }
  }

  function wordById(id) { return S.transcript.words.find(function (w) { return w.id === id; }); }

  function openItem() { return S.review[0] || null; }

  function disputedSet() {
    var set = {};
    S.review.forEach(function (d) { if (d.kind !== "verifier_only") (d.word_ids || []).forEach(function (id) { set[id] = true; }); });
    return set;
  }

  function segments() {
    var words = S.transcript.words, out = [], cur = null;
    words.forEach(function (w) {
      var prev = cur && cur.words[cur.words.length - 1];
      if (!cur || w.speaker_id !== cur.speaker || (prev && w.start_us - prev.end_us > 2000000) || cur.words.length >= 60) {
        cur = { speaker: w.speaker_id, words: [] }; out.push(cur);
      }
      cur.words.push(w);
    });
    return out;
  }

  function listen(startUs, endUs) {
    player.src = mediaUrl("/v1/projects/" + S.pid + "/audio", { start_us: Math.max(0, startUs), end_us: Math.max(endUs, startUs + 200000), pad_us: 600000 });
    player.play().catch(function () { S.flash = "Não consegui tocar o trecho."; render(); });
  }

  async function seekTo(us) {
    if (!host.canPremiere) { S.flash = "Mostrar na sequência funciona dentro do Premiere."; render(); return; }
    try { await host.seek(offset() + us); S.flash = "Indicador movido para " + tc(offset() + us) + "."; }
    catch (e) { S.flash = "Não consegui mover o indicador: " + e.message; }
    render();
  }

  function resolveItem(d, choice) {
    if (d.kind === "verifier_only") {
      if (choice === "keep") return dismiss(d);
      var after = (d.word_ids && d.word_ids[0]) || null;
      return ops([{ kind: "insert_words", after_word_id: after, text: d.alternative_text }], "Inserido.");
    }
    var ids = d.word_ids || [];
    if (choice === "keep") {
      return ops(ids.map(function (id) { return { kind: "replace_text", word_id: id, text: wordById(id).text }; }), "Mantido e travado.");
    }
    var list = [];
    if (d.alternative_text) list.push({ kind: "replace_text", word_id: ids[0], text: d.alternative_text });
    else list.push({ kind: "delete_words", word_ids: [ids[0]] });
    if (ids.length > 1) list.push({ kind: "delete_words", word_ids: ids.slice(1) });
    return ops(list, "Corrigido e travado.");
  }

  async function dismiss(d) {
    try { await api("POST", "/v1/projects/" + S.pid + "/review/" + encodeURIComponent(d.id) + "/dismiss"); await reloadAfterEdit(); }
    catch (e) { S.flash = e.message; }
    render();
  }

  // ------------------------------------------------------------------ estilo
  async function rediarize() {
    if (S.busy || !S.pid) return;
    S.busy = "speakers"; S.cancelRequested = false; render();
    try {
      var body = { num_speakers: S.speakerCount === "auto" ? null : Number(S.speakerCount) };
      var range = S.project.range || {};
      // Mais contexto ajuda a reconhecer vozes que falam pouco no recorte.
      if (host.canPremiere && typeof host.exportAudioContext === "function" &&
          range.end_us && range.end_us - range.start_us < 60000000) {
        var seq = await host.getSequence();
        if (!seq || seq.guid !== S.project.sequence_guid) throw new Error("Volte à sequência desta transcrição para conferir as vozes.");
        var target = await api("POST", "/v1/projects/" + S.pid + "/audio-target");
        var presets = (await host.findAudioPresets()).concat(target.presets || []);
        if (!presets.length) throw new Error("preset de áudio não encontrado");
        var start = Math.max(0, range.start_us - 30000000);
        var end = Math.min(seq.end_us, range.end_us + 30000000);
        var path = target.path.replace(/\.wav$/i, "_contexto_vozes.wav");
        var exported = await host.exportAudioContext(path, presets[0], start, end, seq.guid);
        body.context_audio_path = exported.path;
        body.context_start_us = exported.start_us;
      }
      if (S.cancelRequested) return;
      var submitted = await api("POST", "/v1/projects/" + S.pid + "/diarize", body);
      S.job = { id: submitted.job_id, stage: "speakers", progress: 0 }; render();
      var result = await pollJob(submitted.job_id, function (j) { S.job = j; render(); });
      if (result.state === "cancelled") { toast("Análise cancelada; atribuição anterior preservada."); return; }
      if (result.state !== "done") throw new Error(result.error || "falha ao separar vozes");
      await reloadAfterEdit();
      S.warnings = result.result.warnings || [];
      toast("Vozes conferidas. Texto e correções preservados; atualize a sequência para aplicar as cores.");
    } catch (e) { toast("Não conferi as vozes: " + e.message, "err"); }
    finally { S.busy = null; S.job = null; render(); }
  }

  // ---- presets pessoais (portáveis) -------------------------------------------------
  // Guardam só o estilo. Ajustes por palavra (word_overrides) pertencem ao projeto e
  // a cor por voz é escolha do projeto: nenhum dos dois entra no arquivo.
  function styleOnly(overrides) {
    var out = {};
    Object.keys(overrides || {}).forEach(function (k) { if (k !== "speaker_color_target" && STYLE_RULES[k]) out[k] = overrides[k]; });
    return out;
  }

  function saveOwnStyle() {
    var name = S.presetName.trim().slice(0,60);
    if (!name) { toast("Dê um nome ao seu estilo.", "err"); return; }
    var item = { name: name, preset: S.style.preset, overrides: styleOnly(resolved()), reading: S.style.reading };
    var list = S.savedStyles.filter(function (p) { return p.name.toLowerCase() !== name.toLowerCase(); });
    list.push(item);
    if (!writeList("dc:savedStyles", list.slice(-50))) { toast("Não foi possível salvar o estilo neste painel.", "err"); return; }
    var replaced = list.length - 1 < S.savedStyles.length, dropped = list.length > 50 ? list[0].name : null;
    S.savedStyles = list.slice(-50); S.presetName = "";
    toast(replaced ? "“" + name + "” atualizado em Meus modelos." : dropped ? "Salvo. Limite de 50: “" + dropped + "” saiu da lista." : "Estilo salvo em Meus modelos.");
  }

  function deleteSaved(i) {
    var item = S.savedStyles[i]; if (!item) return;
    var list = S.savedStyles.filter(function (_, j) { return j !== i; });
    if (!writeList("dc:savedStyles", list)) { toast("Não foi possível alterar Meus modelos.", "err"); return; }
    S.savedStyles = list; toast("“" + item.name + "” removido de Meus modelos.");
  }

  // Os visuais que vêm com o plugin também podem ser apagados (somem de Visual; "Restaurar" traz de volta).
  function deletePreset(id) {
    if (S.hiddenPresets.indexOf(id) >= 0) return;
    var list = S.hiddenPresets.concat([id]);
    if (!writeList("dc:hiddenPresets", list)) { toast("Não foi possível alterar Meus modelos.", "err"); return; }
    S.hiddenPresets = list; toast("“" + (STYLE_NAMES[id] || id) + "” apagado.");
  }
  function restorePresets() {
    if (!writeList("dc:hiddenPresets", [])) { toast("Não foi possível alterar Meus modelos.", "err"); return; }
    S.hiddenPresets = []; toast("Visuais do plugin restaurados.");
  }

  function knownPreset(id) { return S.catalog.length ? !!catalogEntry(id) : !!STYLE_NAMES[id]; }

  /* Mesma regra do motor (StyleSpec._temporal_modes). Devolve o motivo ou null. */
  function compatError(spec) {
    if ((spec.karaoke === "fill" || spec.karaoke === "color") && (spec.text_mode !== "phrase" || spec.animation_in === "typewriter"))
      return "destaque acumulado/preenchido exige frase inteira e entrada diferente de digitação";
    if (isAdvanced(spec) && (spec.text_mode === "word" || spec.karaoke === "fill" || spec.karaoke === "color" || spec.animation_in === "typewriter"))
      return "marca-texto exige frase ou revelar, sem destaque acumulado/preenchido nem digitação";
    return null;
  }

  function checkValue(key, value) {
    var rule = STYLE_RULES[key];
    if (!rule) return "campo desconhecido";
    if (rule[0] === "string") return typeof value === "string" && value.trim() && value.length <= 160 && !/[{},\\\r\n;"'<>]/.test(value) ? null : "nome de fonte inválido";
    if (rule[0] === "boolean") return typeof value === "boolean" ? null : "esperava sim/não";
    if (rule[0] === "hex") return typeof value === "string" && HEX.test(value) ? null : "cor inválida";
    if (rule[0] === "enum") return rule.indexOf(value, 1) > 0 ? null : "valor fora da lista";
    if (typeof value !== "number" || !isFinite(value)) return "esperava número";
    return value >= rule[1] && value <= rule[2] ? null : "fora do intervalo " + rule[1] + "–" + rule[2];
  }

  /* Valida um arquivo de estilos antes de qualquer envio ao motor. */
  function validatePresetFile(data) {
    var ok = [], problems = [];
    if (!data || typeof data !== "object" || data.schema !== PRESET_FILE_SCHEMA) return { styles: [], problems: ["não é um arquivo de estilos do Dynamic Captions"] };
    if (data.version !== PRESET_FILE_VERSION) return { styles: [], problems: ["versão " + String(data.version) + " não suportada (este painel lê a versão " + PRESET_FILE_VERSION + ")"] };
    if (!Array.isArray(data.styles)) return { styles: [], problems: ["lista de estilos ausente"] };
    data.styles.slice(0, 50).forEach(function (item, i) {
      var label = item && typeof item.name === "string" && item.name.trim() ? "“" + item.name.trim().slice(0, 60) + "”" : "item " + (i + 1);
      if (!item || typeof item !== "object") { problems.push(label + ": formato inválido"); return; }
      if (typeof item.name !== "string" || !item.name.trim()) { problems.push(label + ": sem nome"); return; }
      if (typeof item.preset !== "string" || !knownPreset(item.preset)) { problems.push(label + ": modelo-base desconhecido (" + String(item.preset) + ")"); return; }
      if (!item.overrides || typeof item.overrides !== "object" || Array.isArray(item.overrides)) { problems.push(label + ": ajustes ausentes"); return; }
      var clean = {}, bad = null, dropped = [];
      Object.keys(item.overrides).forEach(function (k) {
        if (k === "speaker_color_target") return; // cor por voz é escolha do projeto
        if (LEGACY_STYLE_KEYS.indexOf(k) >= 0) return; // hierarquia (até 0.2.11): não existe mais
        if (!STYLE_RULES[k]) { dropped.push(k); return; }
        var why = checkValue(k, item.overrides[k]);
        if (why && !bad) bad = k + ": " + why;
        clean[k] = typeof item.overrides[k] === "string" && STYLE_RULES[k][0] === "hex" ? item.overrides[k].toUpperCase() : item.overrides[k];
      });
      if (bad) { problems.push(label + ": " + bad); return; }
      var base = (catalogEntry(item.preset) || {}).spec || {};
      var incompatible = compatError(Object.assign({}, base, clean));
      if (incompatible) { problems.push(label + ": " + incompatible); return; }
      if (item.word_overrides) problems.push(label + ": ajustes por palavra ignorados (pertencem ao projeto)");
      if (dropped.length) problems.push(label + ": campos ignorados (" + dropped.join(", ") + ")");
      var reading = ["auto", "redes_sociais", "legenda_tradicional"].indexOf(item.reading) >= 0 ? item.reading : undefined;
      ok.push({ name: item.name.trim().slice(0, 60), preset: item.preset, overrides: clean, reading: reading });
    });
    if (data.styles.length > 50) problems.push("só os 50 primeiros estilos foram lidos");
    return { styles: ok, problems: problems };
  }

  function presetFileText() {
    return JSON.stringify({ schema: PRESET_FILE_SCHEMA, version: PRESET_FILE_VERSION, exported_at: new Date().toISOString(),
      styles: S.savedStyles.map(function (p) { return { name: p.name, preset: p.preset, reading: p.reading, overrides: styleOnly(p.overrides) }; }) }, null, 2);
  }

  function cepFs() { return window.cep && window.cep.fs && typeof window.cep.fs.showSaveDialogEx === "function" ? window.cep.fs : null; }

  function exportSaved() {
    if (!S.savedStyles.length) { toast("Salve um estilo em Meus modelos antes de exportar.", "err"); return; }
    var text = presetFileText(), name = "dynamic-captions-estilos.json", fs = cepFs();
    try {
      if (fs) { // painel CEP: diálogo nativo e gravação local
        var chosen = fs.showSaveDialogEx("Exportar estilos do Dynamic Captions", "", ["json"], name);
        if (!chosen || chosen.err || !chosen.data) return;
        var path = /\.json$/i.test(chosen.data) ? chosen.data : chosen.data + ".json";
        var w = fs.writeFile(path, text);
        if (w && w.err) throw new Error("erro " + w.err + " ao gravar");
        toast(S.savedStyles.length + " estilo(s) exportado(s) para " + path + ".");
        return;
      }
      var a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      a.download = name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      toast(S.savedStyles.length + " estilo(s) exportado(s) como " + name + ".");
    } catch (e) { toast("Não exportei os estilos: " + e.message, "err"); }
  }

  function importText(text) {
    var data;
    if (text.length > 262144) { S.importReport = { added: 0, problems: ["arquivo maior que 256 KB"] }; render(); return; }
    try { data = JSON.parse(text); } catch (e) { S.importReport = { added: 0, problems: ["o arquivo não é JSON válido"] }; render(); return; }
    var result = validatePresetFile(data);
    if (result.styles.length) {
      var names = {}; result.styles.forEach(function (p) { names[p.name.toLowerCase()] = true; });
      var list = S.savedStyles.filter(function (p) { return !names[p.name.toLowerCase()]; }).concat(result.styles).slice(-50);
      if (!writeList("dc:savedStyles", list)) result.problems.push("não foi possível guardar neste painel");
      else S.savedStyles = list;
    }
    S.importReport = { added: result.styles.length, problems: result.problems };
    render();
  }

  function importSaved() {
    var fs = cepFs();
    if (fs && typeof fs.showOpenDialogEx === "function") {
      var chosen = fs.showOpenDialogEx(false, false, "Importar estilos do Dynamic Captions", "", ["json"]);
      var path = chosen && !chosen.err && chosen.data && chosen.data[0];
      if (!path) return;
      var read = fs.readFile(path);
      if (!read || read.err) { toast("Não li o arquivo (erro " + (read && read.err) + ").", "err"); return; }
      importText(String(read.data || ""));
      return;
    }
    var input = document.getElementById("importFile");
    if (input) { input.value = ""; input.click(); }
  }

  // ---- estilo do projeto -------------------------------------------------------------
  function isAdvanced(st) { return (st.marker && st.marker !== "none") || (st.active_scale || 1) !== 1; }

  /* Ajusta combinações que o motor recusaria e explica o que mudou. */
  function harmonize(patch) {
    var next = Object.assign({}, resolved(), patch), notes = [];
    var turnsAdvanced = ("marker" in patch && patch.marker !== "none") ||
      ("active_scale" in patch && Number(patch.active_scale) !== 1);
    var turnsTemporal = patch.text_mode === "word" || patch.karaoke === "color" || patch.karaoke === "fill" || patch.animation_in === "typewriter";
    if (turnsAdvanced) {
      if (next.text_mode === "word") { patch.text_mode = "phrase"; notes.push("Exibição voltou para Frase"); }
      if (next.karaoke === "color" || next.karaoke === "fill") { patch.karaoke = "active"; notes.push("Destaque passou para Ativa"); }
      if (next.animation_in === "typewriter") { patch.animation_in = "fade"; notes.push("Digitação trocada por Surgir"); }
    } else if (turnsTemporal && isAdvanced(next)) {
      patch.marker = "none"; patch.active_scale = 1;
      notes.push("Marca-texto desligado");
    }
    next = Object.assign({}, resolved(), patch);
    if ((patch.text_mode && patch.text_mode !== "phrase") || patch.animation_in === "typewriter") {
      if (["color", "fill"].indexOf(next.karaoke) >= 0) patch.karaoke = patch.text_mode === "progressive" ? "active" : "none";
    }
    if (["color", "fill"].indexOf(patch.karaoke) >= 0) {
      if (next.text_mode && next.text_mode !== "phrase") notes.push("Exibição voltou para Frase");
      patch.text_mode = "phrase";
      if (next.animation_in === "typewriter") patch.animation_in = "none";
    }
    return notes;
  }

  var styleTimer = null;
  var styleGeneration = 0;
  function setStyle(patch, presetId) {
    var st = S.style;
    patch = Object.assign({}, patch || {});
    if (presetId) {
      var keep = {};
      // Trocar modelo não muda a cor por voz, a fonte, as cores, o tamanho nem a posição escolhidos
      // (nem os ajustes por palavra deste projeto): as amostras já mostram o estilo com elas.
      ["speaker_color_target"].concat(LOOK_KEYS, PLACE_KEYS).forEach(function (k) { if (st.overrides[k] !== undefined) keep[k] = st.overrides[k]; });
      st.preset = presetId; st.overrides = keep;
      var item = catalogEntry(presetId);
      if (item) st.resolved = Object.assign({}, item.spec);
    }
    if (patch.animation_in === "custom") {
      // Parte da entrada que estava escolhida (a escolha mais recente, mesmo que ainda não salva).
      // Sem equivalente (Salto, Digitação…), ficam os números da última personalizada.
      var was = st.overrides.animation_in !== undefined ? st.overrides.animation_in : (st.resolved || {}).animation_in;
      var from = ENTRY_START[was] || (st.overrides.entry_unit === undefined ? ENTRY_START.blur_rise : null);
      if (from && was !== "custom") Object.keys(from).forEach(function (k) { if (patch[k] === undefined) patch[k] = from[k]; });
    }
    var notes = harmonize(patch);
    Object.keys(patch).forEach(function (k) { st.overrides[k] = patch[k]; });
    if (notes.length) toast(notes.join(" · ") + ": combinação exigida pelo motor.", "good");
    scheduleStyleSave();
  }

  function scheduleStyleSave() {
    var st = S.style, projectId = S.pid, generation = ++styleGeneration;
    if (!st.word_overrides) st.word_overrides = {};
    S.savingStyle = true; S.saveState = "saving"; S.saveError = "";
    clearTimeout(styleTimer);
    // O redesenho vem depois de armar o salvamento: trocar um campo em edição (tamanho em px)
    // dispara outro "change", que agenda de novo; antes esse segundo pedido era cancelado.
    setTimeout(render, 0);
    styleTimer = setTimeout(async function () {
      var words = Object.assign({}, st.word_overrides);
      try {
        // Sempre reenviar word_overrides: o PUT substitui o estilo inteiro do projeto.
        var updated = await api("PUT", "/v1/projects/" + projectId + "/style",
          { preset: st.preset, reading: st.reading, overrides: st.overrides, word_overrides: words });
        if (generation !== styleGeneration || S.pid !== projectId) return;
        if (!updated.word_overrides) updated.word_overrides = words;
        S.style = updated;
        await loadBlocks();
        if (generation !== styleGeneration || S.pid !== projectId) return;
        S.project = await api("GET", "/v1/projects/" + S.pid);
        S.saveState = "saved";
      } catch (e) {
        if (generation === styleGeneration) { S.saveState = "error"; S.saveError = e.message; toast("Não salvei o estilo: " + e.message, "err"); }
      }
      finally { if (generation === styleGeneration) S.savingStyle = false; }
      render();
    }, 250);
  }

  function resolved() {
    var r = Object.assign({}, S.style.resolved || {}, S.style.overrides || {});
    return r;
  }

  // ---- estilo por palavra ------------------------------------------------------------
  function wordStyle(id) { return Object.assign({}, WORD_DEFAULTS, ((S.style && S.style.word_overrides) || {})[id] || {}); }
  function hasWordStyle(id) { return !!(S.style && S.style.word_overrides && S.style.word_overrides[id]); }
  function blockOf(id) { return S.blocks.find(function (b) { return b.lines.some(function (l) { return l.word_ids.indexOf(id) >= 0; }); }) || null; }
  function blockWordIds(b) { return b ? [].concat.apply([], b.lines.map(function (l) { return l.word_ids; })) : []; }
  function letters(text) { return Array.from(String(text || "").replace(/[^\p{L}]/gu, "")).length; }
  function cleanWordPatch(p) {
    var out = {};
    var scale = Number(p.font_scale);
    if (isFinite(scale) && Math.abs(scale - 1) > 1e-9) out.font_scale = Math.min(2.5, Math.max(0.5, Math.round(scale * 100) / 100));
    if (typeof p.font_family === "string" && p.font_family.trim()) out.font_family = p.font_family.trim().slice(0, 160);
    if (p.bold === true || p.bold === false) out.bold = p.bold;
    if (p.italic === true || p.italic === false) out.italic = p.italic;
    if (typeof p.text_color === "string" && HEX.test(p.text_color)) out.text_color = p.text_color.toUpperCase();
    if (p.break_before === true) out.break_before = true;
    return out;
  }

  function setWordStyle(id, patch) {
    if (!S.style || !wordById(id)) return;
    var map = Object.assign({}, S.style.word_overrides || {});
    var cleaned = cleanWordPatch(Object.assign(wordStyle(id), patch));
    if (Object.keys(cleaned).length) map[id] = cleaned; else delete map[id];
    S.style.word_overrides = map;
    scheduleStyleSave();
  }

  function restoreWord(id) {
    if (!hasWordStyle(id)) return;
    var map = Object.assign({}, S.style.word_overrides); delete map[id];
    S.style.word_overrides = map;
    scheduleStyleSave();
    S.flash = "Palavra restaurada ao estilo do modelo.";
  }

  function previewTimeUs() {
    // Meio da palavra: no primeiro quadro dela a animação de entrada ainda não terminou.
    var w = S.sel ? wordById(S.sel) : null;
    var first = S.blocks[0];
    var t = w ? Math.round((w.start_us + w.end_us) / 2) : (first ? first.start_us : 0);
    var b = S.blocks.find(function (block) { return block.start_us <= t && t < block.end_us; });
    var st = resolved();
    // Mesmo instante usado pelo motor: o fundo do Premiere e a legenda devem coincidir.
    if (!b || st.animation_in === "none") return t;
    var frame = Math.round(1000000 / fps()), anim = (st.animation_ms || 0) * 1000;
    var lo = b.start_us + anim + frame;
    var hi = b.end_us - (st.animation_in === "fade" ? anim : 0) - frame;
    return hi <= lo ? Math.floor((b.start_us + b.end_us) / 2) : Math.min(Math.max(t, lo), hi);
  }

  function previewBlock(tUs) {
    return S.blocks.find(function (b) { return tUs >= b.start_us && tUs < b.end_us; }) || S.blocks.find(function (b) { return b.start_us >= tUs; }) || S.blocks[0];
  }


  // ------------------------------------------------------------------ aplicar
  async function apply() {
    if (S.busy || S.savingStyle) return;
    if (S.saveState === "error") { toast("O estilo não foi salvo: " + S.saveError + ". Ajuste e tente de novo.", "err"); return; }
    if (!host.canPremiere) { toast("Aplicar à sequência funciona dentro do Premiere.", "err"); return; }
    var seq = await host.getSequence().catch(function () { return null; });
    if (!seq || seq.guid !== S.project.sequence_guid) {
      toast("Abra a sequência “" + (S.project.sequence && S.project.sequence.name) + "” na Timeline antes de aplicar.", "err"); return;
    }
    S.busy = "apply"; S.applyResult = null; S.cancelRequested = false; S.job = { stage: "render", progress: 0 }; render();
    try {
      if (S.mode === "animated") {
        // A sequência pode ter sido encurtada depois da transcrição: a legenda não passa do fim do conteúdo de agora.
        var job = await api("POST", "/v1/projects/" + S.pid + "/render", { content_end_us: seq.content_end_us || null });
        S.job.id = job.job_id;
        var final = await pollJob(job.job_id, function (j) { S.job.progress = j.progress; render(); });
        if (final.state === "cancelled") { S.applyResult = { cancelled: true }; return; }
        if (final.state !== "done") throw new Error(final.error || "a renderização não terminou");
        S.job.stage = "place"; render();
        var prevPaths = previousManifest().map(function (c) { return c.path; });
        var res;
        try { res = await host.applyAnimated(final.result.clips, S.project.sequence_guid, previousManifest()); }
        catch (pe) {
          // Falha depois de a timeline mudar: guarda o manifesto parcial para o próximo "Aplicar" completar.
          var partial = pe && pe.data && pe.data.partial;
          if (partial && Array.isArray(partial.clips)) {
            // style_key marcado como parcial: o painel continua mostrando "Mudou depois de aplicar"
            // e o botão Aplicar fica disponível para completar.
            var saved = false;
            try {
              await api("PUT", "/v1/projects/" + S.pid + "/applied", { mode: "animated", rev: final.result.rev, style_key: final.result.style_key + ":parcial",
                clips: partial.clips, sequence_guid: partial.sequence_guid, track_index: partial.track_index });
              saved = true;
              S.project = await api("GET", "/v1/projects/" + S.pid);
            } catch (e2) { /* não mascara o erro original */ }
            S.applyResult = { error: pe.message + (saved ? " A legenda ficou incompleta na timeline; aplique de novo para completar."
              : " A legenda ficou incompleta na timeline. Use “Remover legendas” e aplique de novo; se aparecer conflito, apague à mão os clipes DC_ da faixa das legendas.") };
            return;
          }
          throw pe;
        }
        // O manifesto salvo é o que o Premiere confirmou (caminho completo + posição real).
        await api("PUT", "/v1/projects/" + S.pid + "/applied", { mode: "animated", rev: final.result.rev, style_key: final.result.style_key,
          clips: res.clips, sequence_guid: res.sequence_guid, track_index: res.track_index });
        S.applyResult = { mode: "animated", clips: final.result.clips.length, reused: final.result.reused, seconds: final.result.seconds, kept: res.kept, removed: res.removed, placed: res.placed, track: res.track_index + 1 };
        var now = res.clips.map(function (c) { return c.path; });
        releaseClips(prevPaths.filter(function (p) { return now.indexOf(p) < 0; }));
      } else {
        var exp = await api("POST", "/v1/projects/" + S.pid + "/exports", { format: "srt", preset: "auto", style: "projeto", content_end_us: seq.content_end_us || null });
        await host.applyNative(exp.path, offset(), S.project.sequence_guid);
        // A legenda nativa não tira da timeline a animada aplicada antes: o manifesto continua com
        // esses clipes. Com clips: [], "Liberar espaço" os via como sem uso e movia os arquivos,
        // deixando mídia offline no Premiere; "Remover legendas" também deixava de achá-los.
        var keep = previousManifest(), was = S.project.applied || {};
        await api("PUT", "/v1/projects/" + S.pid + "/applied", { mode: "native", rev: exp.rev, style_key: S.style.key, clips: keep,
          sequence_guid: keep.length ? was.sequence_guid : null, track_index: keep.length ? was.track_index : null });
        S.applyResult = { mode: "native" };
      }
      S.project = await api("GET", "/v1/projects/" + S.pid);
    } catch (e) {
      S.applyResult = { error: e.message };
    } finally { S.busy = null; S.job = null; render(); }
  }

  function previousManifest() {
    var a = S.project && S.project.applied;
    // Vale também depois de uma legenda nativa: os clipes animados de antes continuam na timeline.
    return a && a.sequence_guid === S.project.sequence_guid ? (a.clips || []) : [];
  }

  async function removeApplied() {
    if (S.busy || !host.canPremiere) return;
    S.busy = "remove"; render();
    try {
      var gone = previousManifest().map(function (c) { return c.path; });
      var r = await host.removeAnimated(S.project.sequence_guid, previousManifest());
      await api("DELETE", "/v1/projects/" + S.pid + "/applied");
      releaseClips(gone);
      S.project = await api("GET", "/v1/projects/" + S.pid);
      S.applyResult = null;
      toast(r.removed + " clipe(s) de legenda removidos da sequência.");
    } catch (e) { toast("Não consegui remover: " + e.message, "err"); }
    finally { S.busy = null; render(); }
  }

  /* Clipes que saíram da timeline: se nenhuma sequência do projeto aberto os usa, saem do painel
     Projeto e o arquivo vai para descartados/ (apagado depois de 7 dias; volta sozinho se um
     render precisar dele). Falhar aqui nunca atrapalha o Aplicar. */
  async function releaseClips(paths) {
    if (!paths.length || !host.canPremiere || !host.releaseClips) return;
    try {
      var r = await host.releaseClips(paths);
      if (r.released && r.released.length) await api("POST", "/v1/projects/" + S.pid + "/discard", { paths: r.released });
    } catch (e) { /* limpeza é secundária */ }
  }

  // ---- Liberar espaço ---------------------------------------------------------------
  function mb(bytes) {
    var v = (bytes || 0) / 1048576;
    return v >= 1024 ? (v / 1024).toFixed(1).replace(".", ",") + " GB" : Math.round(v) + " MB";
  }
  async function openSpace() {
    if (S.screen !== "space") S.backScreen = S.screen;
    S.screen = "space"; S.space = null; render();
    try { S.space = await api("GET", "/v1/storage"); } catch (e) { S.space = { error: e.message }; }
    render();
  }
  async function freeSpace() {
    if (S.busy || !S.space || S.space.error) return;
    S.busy = "space"; render();
    try {
      var paths = [];
      if (host.canPremiere && host.releaseClips && S.space.unused.paths.length) {
        var r = await host.releaseClips(S.space.unused.paths);  // só os que nenhuma sequência aberta usa
        paths = r.released || [];
      }
      var res = await api("POST", "/v1/storage/clean", { paths: paths });
      toast(mb(res.freed_bytes) + " liberados.");
      S.space = await api("GET", "/v1/storage");
    } catch (e) { toast("Não consegui liberar espaço: " + e.message, "err"); }
    finally { S.busy = null; render(); }
  }
  function screenSpace() {
    var sp = S.space;
    var head = '<div class="sheet-head"><button class="ib" data-act="spaceBack" aria-label="Voltar">' + icon("back") + "</button><h2>Espaço em disco</h2></div>";
    if (!sp) return '<div class="sheet space">' + head + '<p class="lead">Medindo…</p></div>';
    if (sp.error) return '<div class="sheet space">' + head + '<p class="alert warn">' + icon("warn") + "<span>" + esc(sp.error) + "</span></p></div>";
    var inUse = Math.max(0, sp.renders_bytes - sp.unused.bytes);
    var free = sp.unused.bytes + sp.discarded_bytes + sp.cache_bytes;
    var row = function (label, value, note) { return '<div class="srow"><span>' + label + (note ? "<small>" + note + "</small>" : "") + "</span><b>" + value + "</b></div>"; };
    return '<div class="sheet space">' + head +
      '<p class="lead">Os clipes de legenda ficam numa pasta do Dynamic Captions. Nada que esteja numa sequência do projeto aberto é apagado.</p>' +
      '<div class="block">' +
        row("Na timeline", mb(inUse), "legendas aplicadas") +
        row("Fora de uso", mb(sp.unused.bytes), sp.unused.count + " clipe(s) que nenhuma legenda aplicada usa") +
        row("Descartados", mb(sp.discarded_bytes), "saíram da timeline; somem sozinhos em 7 dias") +
        row("Prévias e quadros", mb(sp.cache_bytes), "refeitos quando precisar") +
      "</div>" +
      '<button class="btn dark" data-act="freeSpace" ' + (S.busy || !free ? "disabled" : "") + ">" + icon("trash") + (S.busy === "space" ? "Liberando…" : "Liberar " + mb(free)) + "</button>" +
      '<p class="hint">Apaga de vez. Um Auto-Save antigo do Premiere que use esses clipes vai mostrá-los como mídia offline' + (host.canPremiere ? "" : ". Fora do Premiere, só descartados, prévias e quadros são apagados") + ".</p></div>";
  }

  // ------------------------------------------------------------------ ícones (Phosphor, MIT)
  var ICONS = {
    "eye": "<path d=\"M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z\"/>",
    "eyeOff": "<path d=\"M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Z\"/><path d=\"M50 30 L226 214 L212 226 L36 42 Z\"/>",
    "undo": "<path d=\"M232,144a64.07,64.07,0,0,1-64,64H80a8,8,0,0,1,0-16h88a48,48,0,0,0,0-96H51.31l34.35,34.34a8,8,0,0,1-11.32,11.32l-48-48a8,8,0,0,1,0-11.32l48-48A8,8,0,0,1,85.66,45.66L51.31,80H168A64.07,64.07,0,0,1,232,144Z\"/>",
    "search": "<path d=\"M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z\"/>",
    "check": "<path d=\"M232.49,80.49l-128,128a12,12,0,0,1-17,0l-56-56a12,12,0,1,1,17-17L96,183,215.51,63.51a12,12,0,0,1,17,17Z\"/>",
    "chevDown": "<path d=\"M216.49,104.49l-80,80a12,12,0,0,1-17,0l-80-80a12,12,0,0,1,17-17L128,159l71.51-71.52a12,12,0,0,1,17,17Z\"/>",
    "chevRight": "<path d=\"M184.49,136.49l-80,80a12,12,0,0,1-17-17L159,128,87.51,56.49a12,12,0,1,1,17-17l80,80A12,12,0,0,1,184.49,136.49Z\"/>",
    "play": "<path d=\"M240,128a15.74,15.74,0,0,1-7.6,13.51L88.32,229.65a16,16,0,0,1-16.2.3A15.86,15.86,0,0,1,64,216.13V39.87a15.86,15.86,0,0,1,8.12-13.82,16,16,0,0,1,16.2.3L232.4,114.49A15.74,15.74,0,0,1,240,128Z\"/>",
    "x": "<path d=\"M208.49,191.51a12,12,0,0,1-17,17L128,145,64.49,208.49a12,12,0,0,1-17-17L111,128,47.51,64.49a12,12,0,0,1,17-17L128,111l63.51-63.52a12,12,0,0,1,17,17L145,128Z\"/>",
    "upload": "<path d=\"M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0ZM93.66,77.66,120,51.31V144a8,8,0,0,0,16,0V51.31l26.34,26.35a8,8,0,0,0,11.32-11.32l-40-40a8,8,0,0,0-11.32,0l-40,40A8,8,0,0,0,93.66,77.66Z\"/>",
    "download": "<path d=\"M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0Zm-101.66,5.66a8,8,0,0,0,11.32,0l40-40a8,8,0,0,0-11.32-11.32L136,124.69V32a8,8,0,0,0-16,0v92.69L93.66,98.34a8,8,0,0,0-11.32,11.32Z\"/>",
    "restore": "<path d=\"M224,128a96,96,0,0,1-94.71,96H128A95.38,95.38,0,0,1,62.1,197.8a8,8,0,0,1,11-11.63A80,80,0,1,0,71.43,71.39a3.07,3.07,0,0,1-.26.25L44.59,96H72a8,8,0,0,1,0,16H24a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0V85.8L60.25,60A96,96,0,0,1,224,128Z\"/>",
    "target": "<path d=\"M232,120h-8.34A96.14,96.14,0,0,0,136,32.34V24a8,8,0,0,0-16,0v8.34A96.14,96.14,0,0,0,32.34,120H24a8,8,0,0,0,0,16h8.34A96.14,96.14,0,0,0,120,223.66V232a8,8,0,0,0,16,0v-8.34A96.14,96.14,0,0,0,223.66,136H232a8,8,0,0,0,0-16Zm-96,87.6V200a8,8,0,0,0-16,0v7.6A80.15,80.15,0,0,1,48.4,136H56a8,8,0,0,0,0-16H48.4A80.15,80.15,0,0,1,120,48.4V56a8,8,0,0,0,16,0V48.4A80.15,80.15,0,0,1,207.6,120H200a8,8,0,0,0,0,16h7.6A80.15,80.15,0,0,1,136,207.6ZM128,88a40,40,0,1,0,40,40A40,40,0,0,0,128,88Zm0,64a24,24,0,1,1,24-24A24,24,0,0,1,128,152Z\"/>",
    "volume": "<path d=\"M155.51,24.81a8,8,0,0,0-8.42.88L77.25,80H32A16,16,0,0,0,16,96v64a16,16,0,0,0,16,16H77.25l69.84,54.31A8,8,0,0,0,160,224V32A8,8,0,0,0,155.51,24.81ZM32,96H72v64H32ZM144,207.64,88,164.09V91.91l56-43.55Zm54-106.08a40,40,0,0,1,0,52.88,8,8,0,0,1-12-10.58,24,24,0,0,0,0-31.72,8,8,0,0,1,12-10.58ZM248,128a79.9,79.9,0,0,1-20.37,53.34,8,8,0,0,1-11.92-10.67,64,64,0,0,0,0-85.33,8,8,0,1,1,11.92-10.67A79.83,79.83,0,0,1,248,128Z\"/>",
    "refresh": "<path d=\"M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z\"/>",
    "back": "<path d=\"M228,128a12,12,0,0,1-12,12H69l51.52,51.51a12,12,0,0,1-17,17l-72-72a12,12,0,0,1,0-17l72-72a12,12,0,0,1,17,17L69,116H216A12,12,0,0,1,228,128Z\"/>",
    "plus": "<path d=\"M228,128a12,12,0,0,1-12,12H140v76a12,12,0,0,1-24,0V140H40a12,12,0,0,1,0-24h76V40a12,12,0,0,1,24,0v76h76A12,12,0,0,1,228,128Z\"/>",
    "more": "<path d=\"M144,128a16,16,0,1,1-16-16A16,16,0,0,1,144,128ZM60,112a16,16,0,1,0,16,16A16,16,0,0,0,60,112Zm136,0a16,16,0,1,0,16,16A16,16,0,0,0,196,112Z\"/>",
    "voices": "<path d=\"M244.8,150.4a8,8,0,0,1-11.2-1.6A51.6,51.6,0,0,0,192,128a8,8,0,0,1-7.37-4.89,8,8,0,0,1,0-6.22A8,8,0,0,1,192,112a24,24,0,1,0-23.24-30,8,8,0,1,1-15.5-4A40,40,0,1,1,219,117.51a67.94,67.94,0,0,1,27.43,21.68A8,8,0,0,1,244.8,150.4ZM190.92,212a8,8,0,1,1-13.84,8,57,57,0,0,0-98.16,0,8,8,0,1,1-13.84-8,72.06,72.06,0,0,1,33.74-29.92,48,48,0,1,1,58.36,0A72.06,72.06,0,0,1,190.92,212ZM128,176a32,32,0,1,0-32-32A32,32,0,0,0,128,176ZM72,120a8,8,0,0,0-8-8A24,24,0,1,1,87.24,82a8,8,0,1,0,15.5-4A40,40,0,1,0,37,117.51,67.94,67.94,0,0,0,9.6,139.19a8,8,0,1,0,12.8,9.61A51.6,51.6,0,0,1,64,128,8,8,0,0,0,72,120Z\"/>",
    "expand": "<path d=\"M216,48V96a8,8,0,0,1-16,0V67.31l-50.34,50.35a8,8,0,0,1-11.32-11.32L188.69,56H160a8,8,0,0,1,0-16h48A8,8,0,0,1,216,48ZM106.34,138.34,56,188.69V160a8,8,0,0,0-16,0v48a8,8,0,0,0,8,8H96a8,8,0,0,0,0-16H67.31l50.35-50.34a8,8,0,0,0-11.32-11.32Z\"/>",
    "trash": "<path d=\"M216,48H176V40a24,24,0,0,0-24-24H104A24,24,0,0,0,80,40v8H40a8,8,0,0,0,0,16h8V208a16,16,0,0,0,16,16H192a16,16,0,0,0,16-16V64h8a8,8,0,0,0,0-16ZM96,40a8,8,0,0,1,8-8h48a8,8,0,0,1,8,8v8H96Zm96,168H64V64H192ZM112,104v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Zm48,0v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Z\"/>",
    "warn": "<path d=\"M236.8,188.09,149.35,36.22h0a24.76,24.76,0,0,0-42.7,0L19.2,188.09a23.51,23.51,0,0,0,0,23.72A24.35,24.35,0,0,0,40.55,224h174.9a24.35,24.35,0,0,0,21.33-12.19A23.51,23.51,0,0,0,236.8,188.09ZM222.93,203.8a8.5,8.5,0,0,1-7.48,4.2H40.55a8.5,8.5,0,0,1-7.48-4.2,7.59,7.59,0,0,1,0-7.72L120.52,44.21a8.75,8.75,0,0,1,15,0l87.45,151.87A7.59,7.59,0,0,1,222.93,203.8ZM120,144V104a8,8,0,0,1,16,0v40a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,180Z\"/>",
    };
  function icon(name, cls) {
    return '<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false">' + (ICONS[name] || "") + "</svg>";
  }

  // ================================================================== interface 0.2.0 · "documento"
  // A fala é o documento; o quadro real fica em cima; estilos aparecem como amostras de texto.
  function wide() { return window.innerWidth >= 900; }

  // O Windows (Controle Inteligente de Aplicativos ou política WDAC) recusou um arquivo do motor.
  function windowsBlocked() {
    var err = S.engine && S.engine.models === "error" ? String(S.engine.error || "") : "";
    return /4551|Controle de Aplicativo|Application Control/i.test(err);
  }

  function engineState() {
    if (S.phase === "connecting") return ["wait", "conectando"];
    if (S.phase !== "ready") return ["bad", "indisponível"];
    var m = S.engine && S.engine.models;
    if (m === "loading") return ["wait", "carregando modelos"];
    if (m === "error") return ["bad", windowsBlocked() ? "bloqueado pelo Windows" : "modelos com erro"];
    return ["ok", "pronto"];
  }
  function statusChip() {
    var e = engineState();
    return '<span class="eng ' + e[0] + '" role="status" title="' + esc(e[0] === "bad" && S.engine ? S.engine.error || e[1] : e[1]) + '"><i></i><span>' + e[1] + "</span></span>";
  }

  function header() {
    var seq = S.seq;
    var name = S.project && S.project.sequence ? S.project.sequence.name : seq ? seq.name : (host.canPremiere ? "Sem sequência" : "Dynamic Captions");
    var range = "";
    if (S.project && S.project.range) {
      var r = S.project.range;
      range = tc(offset()) + " – " + (r.end_us != null ? tc(r.end_us) : "fim");
    } else if (seq) range = S.scope === "inout" && seq.in_us != null ? tc(seq.in_us) + " – " + tc(seq.out_us) : tc(0) + " – " + tc(seq.end_us);
    var applied = S.project && S.project.applied;
    var menu = S.menu ? '<div class="menu" role="menu">' +
      '<button role="menuitem" data-act="refresh">' + icon("refresh") + "Ler a sequência de novo</button>" +
      (S.conn ? '<button role="menuitem" data-act="space">' + icon("trash") + "Liberar espaço</button>" : "") +
      (S.transcript && S.conn ? '<button role="menuitem" data-act="wide">' + icon("expand") + "Abrir em janela grande</button>" : "") +
      (applied && (applied.clips || []).length && host.canPremiere ? '<button role="menuitem" data-act="remove" class="danger">' + icon("trash") + "Tirar legendas da sequência</button>" : "") +
      "</div>" : "";
    return '<header class="top"><div class="ttl"><h1 title="' + esc(name) + '">' + esc(name) + "</h1>" + (range ? '<span class="tc">' + esc(range) + "</span>" : "") + "</div>" +
      statusChip() + '<button class="ib" data-act="menu" aria-haspopup="menu" aria-expanded="' + !!S.menu + '" aria-label="Mais ações">' + icon("more") + "</button>" + menu + "</header>";
  }

  // ---- quadro (prévia) ---------------------------------------------------------------
  function frameBox() {
    var seq = (S.project && S.project.sequence) || { width: 1920, height: 1080 };
    var aspect = seq.width / seq.height, H = window.innerHeight, W = window.innerWidth;
    // O quadro ocupa toda a largura disponível (sem faixas pretas). Só um vídeo vertical muito alto
    // é limitado pela altura do painel; aí ele fica centralizado sobre o fundo do painel.
    var availW = S.frameW || (wide() ? Math.max(320, W - 448) : W - 24);
    var maxH = wide() ? H * 0.62 : H * 0.5;
    var w = availW, h = availW / aspect;
    if (h > maxH) { h = maxH; w = h * aspect; }
    return { w: Math.round(w), h: Math.round(h), seq: seq, vertical: seq.height > seq.width };
  }

  function rgba(hex, alpha) {
    var h = HEX.test(hex || "") ? hex : "#000000";
    return "rgba(" + [1,3,5].map(function (i) { return parseInt(h.slice(i,i+2),16); }).join(",") + "," + alpha + ")";
  }

  /* Arco (mesma conta do motor, rich_ass._arch_at): cada letra sobe, gira e cresce conforme a
     distância ao centro da linha. Medidas em px da sequência; k converte para a prévia. */
  function archLetters(tokens, tok, arch, k, text) {
    var row = function (o) { return Math.round(o.y + o.height / 2); };  // pelo centro (palavra com tamanho próprio)
    var line = tokens.filter(function (o) { return row(o) === row(tok); });
    var L = Math.min.apply(null, line.map(function (o) { return o.x; }));
    var R = Math.max.apply(null, line.map(function (o) { return o.x + o.width; }));
    var hw = Math.max(1, (R - L) / 2), cx = (L + R) / 2, sag = arch / 100 * (R - L);
    var chars = Array.from(text);
    return chars.map(function (ch, i) {
      var x = tok.x + (i + 0.5) / chars.length * tok.width, u = Math.max(-1, Math.min(1, (x - cx) / hw));
      var dy = -sag * (1 - u * u) * k, deg = Math.atan(2 * sag * u / hw) * 180 / Math.PI, bump = 1 + 5 * sag / (2 * hw) * (1 - u * u);
      return '<span class="arc-l" style="transform:translateY(' + dy.toFixed(1) + 'px) rotate(' + deg.toFixed(2) + 'deg) scale(' + bump.toFixed(3) + ')">' + esc(ch) + "</span>";
    }).join("");
  }

  /* Negrito é 700 em todo o painel: no Windows o Chrome do CEP resolve 800 do Arial como Arial Black (mais larga),
     e a prévia saía com as palavras coladas, diferente do vídeo (Arial Negrito).
     Prévia rápida com a geometria do motor (layouts): cada palavra fica parada durante a fala;
     só cor, marca-texto e escala da palavra ativa mudam. */
  function layoutHtml(layout, block, st, boxH, seq, words, activeIdx, hl, shadow) {
    var k = boxH / seq.height;
    var ids = blockWordIds(block);
    var activeId = activeIdx >= 0 ? ids[activeIdx] : null;
    var activeWord = activeId ? wordById(activeId) : null;
    var t = activeWord ? Math.round((activeWord.start_us + activeWord.end_us) / 2) : -1;
    var pad = (st.marker_padding_px || 0) * k;
    var html = "";
    layout.tokens.forEach(function (tok) {
      var w = wordById(tok.word_id);
      var active = !!(w && t >= w.start_us && t < w.end_us);
      var order = ids.indexOf(tok.word_id);
      var hidden = st.text_mode === "progressive" && activeIdx >= 0 && order > activeIdx;
      var left = tok.x / seq.width * 100, top = tok.y / seq.height * 100;
      var width = tok.width / seq.width * 100, height = tok.height / seq.height * 100;
      var label = esc(st.uppercase ? tok.text.toUpperCase() : tok.text);
      if (st.arch_pct && !st.wobble) label = archLetters(layout.tokens, tok, st.arch_pct, k, st.uppercase ? tok.text.toUpperCase() : tok.text);
      if (active && st.marker === "active" && !hidden) {
        html += '<i class="marker" style="left:calc(' + left.toFixed(3) + '% - ' + pad.toFixed(1) + 'px);top:calc(' + top.toFixed(3) + '% - ' + pad.toFixed(1) + 'px);width:calc(' + width.toFixed(3) + '% + ' + (2 * pad).toFixed(1) + 'px);height:calc(' + height.toFixed(3) + '% + ' + (2 * pad).toFixed(1) + 'px);border-radius:' + ((st.marker_radius_px || 0) * k).toFixed(1) + 'px;background:' + rgba(st.marker_color, st.marker_opacity == null ? 1 : st.marker_opacity) + '"></i>';
      }
      var color = active && st.karaoke !== "none" ? hl : (tok.text_color || st.text_color);
      if (active && st.marker === "active") color = markedColor(color, st.marker_color);
      var scale = active && (st.active_scale || 1) !== 1 ? "transform:scale(" + st.active_scale + ");" : "";
      html += '<span class="tok' + (S.sel === tok.word_id ? " picked" : "") + '" data-act="pick" data-v="' + esc(tok.word_id) + '" style="left:' + left.toFixed(3) + '%;top:' + top.toFixed(3) + '%;width:' + width.toFixed(3) + '%;height:' + height.toFixed(3) + '%;font-size:' + ((tok.em_size || tok.font_size * 0.9) * k).toFixed(1) + 'px;line-height:' + (tok.height * k).toFixed(1) + 'px;font-family:\'' + esc(tok.font_family || st.font_family) + '\',sans-serif;font-weight:' + (tok.bold ? 700 : 400) + ';font-style:' + (tok.italic ? "italic" : "normal") + ';color:' + esc(color) + ';' + scale + shadow + (hidden ? "visibility:hidden;" : "") + '">' + label + '</span>';
    });
    return '<div class="stage-box" style="aspect-ratio:' + seq.width + '/' + seq.height + '">' + html + '</div>';
  }

  function frameHtml() {
    if (!S.transcript || !S.style) return "";
    var box = frameBox(), seq = box.seq;
    var st = resolved();
    var tUs = previewTimeUs();
    var block = previewBlock(tUs);
    var words = block ? block.lines.map(function (l) { return l.word_ids.map(wordById).filter(Boolean); }) : [];
    var flat = [].concat.apply([], words);
    var activeIdx = S.sel ? flat.findIndex(function (w) { return w.id === S.sel; }) : (S.karaokeTick % Math.max(flat.length, 1));
    var sp = block ? speaker(block.speaker_id) : null;
    var ec = effectiveColors(st, sp), hl = ec.hl, txt = ec.txt;
    var boxH = box.h;
    var fontPx = Math.max(6, boxH * st.font_size_pct / 100 * 0.9);  // \fs do render é a altura da linha; o corpo é ~90%
    var pos = st.position === "top" ? "top:" + Math.max(st.margin_pct, 4) + "%" : st.position === "middle" ? "top:50%;transform:translateY(-50%)" : "bottom:" + Math.max(st.margin_pct, 4) + "%";
    var k1080 = boxH / 1080;
    var shadow = st.box ? "" : "text-shadow:0 0 " + Math.max(1, st.outline_px * k1080) + "px " + st.outline_color + "," + [-1, 1].map(function (d) { return d * Math.max(.6, st.outline_px * k1080 * .6) + "px 0 " + st.outline_color; }).join(",") + ",0 " + Math.max(.6, st.outline_px * k1080 * .6) + "px " + st.outline_color + ";";
    if (st.glow_px) shadow = "text-shadow:0 0 3px " + st.outline_color + ",0 0 " + st.glow_px * k1080 * 3 + "px " + st.outline_color + ";";
    var layout = block && S.layouts && S.layouts[block.id];
    var caption = "";
    if (layout && layout.tokens && layout.tokens.length) {
      caption = layoutHtml(layout, block, st, boxH, seq, words, activeIdx, hl, shadow);
    } else {
      var k = 0;
      var lines = words.map(function (line) {
        return line.map(function (w) {
          var index = k++;
          if (st.text_mode === "word" && index !== activeIdx) return "";
          var on = st.karaoke === "active" ? index === activeIdx : st.karaoke !== "none" && index <= activeIdx;
          var t = st.uppercase ? w.text.toUpperCase() : w.text;
          var hidden = st.text_mode === "progressive" && index > activeIdx;
          var marked = st.marker === "active" && index === activeIdx;
          var pulse = index === activeIdx && (st.active_scale || 1) !== 1;
          return '<span data-act="pick" data-v="' + esc(w.id) + '" class="' + (S.sel === w.id ? "picked" : "") + '" style="color:' + (on ? hl : txt) + (hidden ? ';visibility:hidden' : '') +
            (marked ? ";background:" + rgba(st.marker_color, st.marker_opacity == null ? 1 : st.marker_opacity) + ";border-radius:" + Math.round((st.marker_radius_px || 0) * k1080) + "px" : "") +
            (pulse ? ";display:inline-block;transform:scale(" + st.active_scale + ")" : "") + '">' + esc(t) + "</span>";
        }).join(" ");
      }).join("<br>");
      var boxCss = st.box ? "background:" + rgba(st.box_color || "#000000", st.box_opacity) + ";padding:.1em .45em;border-radius:3px;" : "";
      // A entrada toca quando o bloco ou o estilo muda; o tique do karaokê não a repete.
      // A legenda da prévia fica parada: repetir a entrada a cada redesenho fazia o quadro piscar.
      // A entrada de cada estilo aparece na amostra sob o ponteiro.
      var anim = "none";
      caption = '<div class="cap" style="' + pos + '"><div class="caption-motion motion-' + anim + (st.wobble ? " wobble" : "") + '" style="--motion-duration:' + st.animation_ms + 'ms;font-family:\'' + esc(st.font_family) + '\',sans-serif;font-weight:' + (st.bold ? 700 : 400) + ";font-style:" + (st.italic ? "italic" : "normal") + ";letter-spacing:" + ((st.spacing_px || 0) * k1080).toFixed(2) + "px;font-size:" + fontPx.toFixed(1) + "px;line-height:1.2;" + shadow + boxCss + '">' + lines + "</div></div>";
    }
    var dx = st.offset_x_pct || 0, dy = st.offset_y_pct || 0;
    var frameBg = S.frameUrl ? "background-image:url('" + S.frameUrl + "');" : "";
    var moved = !!(dx || dy);
    var tools = '<div class="fr-tools">' + (moved ? '<button class="fr-btn" data-act="resetPos" title="Voltar à posição do modelo">' + icon("target") + "Centralizar</button>" : "") +
      '<button class="fr-btn icon show" data-act="safe" aria-pressed="' + !!S.safe + '" aria-label="' + (S.safe ? "Ocultar margens de segurança" : "Mostrar margens de segurança") + '" title="' + (S.safe ? "Ocultar margens" : "Mostrar margens") + '">' + icon(S.safe ? "eye" : "eyeOff") + "</button></div>";
    return '<div class="frame-wrap"><div class="frame' + (S.frameUrl ? " has-frame" : "") + '" data-bg="' + esc(S.frameUrl || "") + '" data-layout="' + (layout ? "motor" : "aproximada") + '" style="width:' + box.w + "px;height:" + box.h + "px;" + frameBg + '">' +
      ('<div class="layer" data-drag="caption" title="Arraste para posicionar · dois toques centralizam" style="transform:translate(' + dx + "%," + dy + '%)">' + caption + "</div>") +
      '<i class="guide gx" aria-hidden="true"></i><i class="guide gy" aria-hidden="true"></i>' +
      (S.safe ? '<div class="safe" aria-hidden="true"></div>' : "") +
      (box.w >= 150 ? '<span class="stamp">' + tc(offset() + tUs) + "</span>" : "") + tools +
      "</div></div>";
  }

  function previewCard() {
    if (!S.transcript || !S.style) return "";
    return '<section class="preview" aria-label="Prévia sobre o quadro da sequência">' + frameHtml() + "</section>";
  }

  /* Redesenhar sem piscar: se o quadro de fundo é o mesmo, o elemento antigo fica (a foto não
     é recarregada) e só a legenda por cima é trocada. */
  function reuseFrame(oldFrame) {
    var nf = $app.querySelector(".frame");
    if (!oldFrame || !nf || oldFrame === nf) return;
    if (!oldFrame.getAttribute("data-bg") || oldFrame.getAttribute("data-bg") !== nf.getAttribute("data-bg")) return;
    ["style", "class", "data-layout"].forEach(function (k) { if (oldFrame.getAttribute(k) !== nf.getAttribute(k)) oldFrame.setAttribute(k, nf.getAttribute(k)); });
    while (oldFrame.firstChild) oldFrame.removeChild(oldFrame.firstChild);
    while (nf.firstChild) oldFrame.appendChild(nf.firstChild);
    nf.replaceWith(oldFrame);
  }

  function refreshFrame() {
    var wrap = $app.querySelector(".frame-wrap"); if (!wrap) return;
    var oldFrame = wrap.querySelector(".frame");
    var t = document.createElement("div"); t.innerHTML = frameHtml();
    if (!t.firstChild) return;
    wrap.replaceWith(t.firstChild);
    reuseFrame(oldFrame);
  }

  /* A largura real do quadro só é conhecida depois de montar a tela. */
  function measureFrame() {
    var pv = $app.querySelector(".preview"); if (!pv) return;
    var cw = pv.clientWidth;
    if (cw && Math.abs(cw - (S.frameW || 0)) > 1) { S.frameW = cw; refreshFrame(); }
  }

  // ---- amostras de estilo (espécimes) -----------------------------------------------
  function sampleWords() {
    // Uma frase do projeto com pelo menos 3 palavras (a primeira legenda pode ter uma só).
    var b = (S.blocks || []).find(function (x) { return blockWordIds(x).length >= 3; }) || (S.blocks && S.blocks[0]);
    var words = b ? blockWordIds(b).map(wordById).filter(Boolean).map(function (w) { return w.text; }) : [];
    if (words.length < 3 && S.transcript) words = S.transcript.words.slice(0, 4).map(function (w) { return w.text; });
    return words.length ? words.slice(0, 4) : ["Sua", "legenda", "aparece", "aqui"];
  }

  /* step: palavra falada no momento (reprodução ao passar o mouse); sem step, um quadro parado típico.
     entering: aplica a entrada do estilo (só no começo de cada volta). */
  function specimen(spec, step, entering) {
    var words = sampleWords();
    var up = function (t) { return spec.uppercase ? String(t).toUpperCase() : String(t); };
    var mainIdx = 0;
    words.forEach(function (w, i) { if (letters(w) > letters(words[mainIdx])) mainIdx = i; });
    var hl = esc(spec.highlight_color || "#FFD400");
    var base = 'font-family:\'' + esc(spec.font_family || "Arial") + '\',sans-serif;font-weight:' + (spec.bold === false ? 400 : 700) + ';font-style:' + (spec.italic ? "italic" : "normal") + ';color:' + esc(spec.text_color || "#fff") + ';' +
      (spec.spacing_px ? "letter-spacing:" + Math.min(3, spec.spacing_px / 3) + "px;" : "");
    var deco = spec.box ? "background:" + rgba(spec.box_color || "#000000", spec.box_opacity == null ? .6 : Math.max(.35, spec.box_opacity)) + ";padding:2px 8px;border-radius:3px;box-shadow:0 0 0 1px rgba(255,255,255,.12);" :
      (spec.glow_px ? "text-shadow:0 0 3px " + esc(spec.outline_color) + ",0 0 10px " + esc(spec.outline_color) + ";" : "");
    // Arco na amostra: a frase toda numa curva (letra por letra), como o motor desenha.
    var arcTotal = words.map(up).join(" ").length, arcStart = [], arcAt = 0;
    words.forEach(function (w) { arcStart.push(arcAt); arcAt += up(w).length + 1; });
    function arcWord(i) {
      var a = spec.arch_pct || 0, chars = Array.from(up(words[i])), wEm = arcTotal * 0.55;
      return chars.map(function (ch, j) {
        var u = ((arcStart[i] + j + 0.5) / arcTotal) * 2 - 1, sag = a / 100 * wEm;
        var dy = -sag * (1 - u * u), deg = Math.atan(2 * sag * u / (wEm / 2)) * 180 / Math.PI, bump = 1 + 5 * a / 100 * (1 - u * u);
        return '<span class="arc-l" style="transform:translateY(' + dy.toFixed(3) + 'em) rotate(' + deg.toFixed(2) + 'deg) scale(' + bump.toFixed(3) + ')">' + esc(ch) + "</span>";
      }).join("");
    }
    function span(i, extra) { return '<span' + (extra ? ' style="' + extra + '"' : "") + ">" + (spec.arch_pct && !spec.wobble ? arcWord(i) : esc(up(words[i]))) + "</span>"; }
    var playing = step != null && step >= 0;
    var act = playing ? Math.min(step, words.length - 1) : Math.min(1, words.length - 1);
    var inner;
    if (spec.text_mode === "word") {
      inner = '<span class="l-solo" style="color:' + (spec.karaoke !== "none" ? hl : "inherit") + '">' + esc(up(words[playing ? act : mainIdx])) + "</span>";
    } else {
      inner = words.map(function (w, i) {
        var active = i === act;
        if (spec.marker === "active" && active) return '<mark style="background:' + rgba(spec.marker_color, spec.marker_opacity == null ? 1 : spec.marker_opacity) + ';color:#111;border-radius:3px;padding:0 3px">' + esc(up(w)) + "</mark>";
        if ((spec.active_scale || 1) > 1 && active) return '<span class="pulse" style="color:' + hl + '">' + esc(up(w)) + "</span>";
        if (spec.karaoke === "active" && active) return span(i, "color:" + hl);
        if ((spec.karaoke === "color" || spec.karaoke === "fill") && i <= act) return span(i, "color:" + hl);
        if (spec.text_mode === "progressive" && i > act) return span(i, "opacity:.22");
        return span(i);
      }).join(" ");
    }
    // Palavra por palavra: cada palavra nova entra com o efeito; nos demais, só no começo da volta.
    var motion = entering || (spec.text_mode === "word" && playing) ? spec.animation_in || "none" : "none";
    var solo = spec.text_mode === "word" && playing ? " solo-step" : "";
    return '<span class="ink motion-' + esc(motion) + solo + (spec.wobble ? " wobble" : "") + '" style="' + base + deco + '">' + inner + "</span>";
  }

  function withLook(spec) {
    var o = (S.style && S.style.overrides) || {}, out = Object.assign({}, spec);
    LOOK_KEYS.forEach(function (k) { if (o[k] !== undefined) out[k] = o[k]; });
    return out;
  }

  function catalogItems() {
    var ids = S.catalog.length ? S.catalog.map(function (p) { return p.id; }) : Object.keys(STYLE_NAMES);
    ids = ids.filter(function (id) {
      if (S.hiddenPresets.indexOf(id) >= 0) return false;
      var e = catalogEntry(id); return !(e && e.hidden) || (S.style && S.style.preset === id);
    });
    return ids.map(function (id) {
      var e = catalogEntry(id) || { spec: {}, category: "essenciais", lines: 0, tags: [] };
      return { kind: "preset", id: id, name: STYLE_NAMES[id] || e.name || id, desc: STYLE_DESCRIPTIONS[id] || "Estilo de legenda",
        spec: withLook(e.spec || {}), category: e.category || "essenciais", lines: e.lines || 0, tags: e.tags || [] };
    });
  }
  function savedItems() {
    return S.savedStyles.map(function (p, i) {
      var spec = Object.assign({}, (catalogEntry(p.preset) || {}).spec || {}, p.overrides);
      return { kind: "saved", id: "saved:" + i, index: i, name: p.name, desc: "Meu modelo · base " + (STYLE_NAMES[p.preset] || p.preset), spec: spec,
        category: (catalogEntry(p.preset) || {}).category || "essenciais", lines: 0, tags: [] };
    });
  }

  function specRow(it, opts) {
    opts = opts || {};
    var selected = it.kind === "preset" && S.style.preset === it.id && !customAnimation();
    // "playing" só entra pelo ponteiro (playCard), nunca no redesenho: senão a amostra repetia a entrada a cada clique.
    var main = '<button class="spec" data-card="' + esc(it.id) + '" aria-pressed="' + selected + '" ' +
      (opts.rover != null ? 'tabindex="' + (it.id === opts.rover ? 0 : -1) + '" ' : "") +
      (it.kind === "preset" ? 'data-act="preset" data-v="' + esc(it.id) + '" aria-keyshortcuts="Delete"' : 'data-act="savedPreset" data-v="' + it.index + '"') +
      ' aria-label="' + esc(it.name) + '" title="' + esc(it.name) + '">' +
      '<span class="sp-chip">' + specimen(it.spec) + "</span>" +
      (selected ? '<span class="sp-check" aria-hidden="true">' + icon("check") + "</span>" : "") +
      (opts.tools ? '<span class="sp-name">' + esc(it.name) + "</span>" : "") + "</button>";
    if (!opts.tools) return '<div class="spec-row' + (selected ? " is-sel" : "") + '">' + main + "</div>";
    // Primeiro toque arma ("Apagar?"), o segundo apaga.
    var armed = S.confirmDel === it.id;
    var side = '<button class="fav' + (armed ? " armed" : "") + '" tabindex="-1" data-act="' + (it.kind === "preset" ? "deletePreset" : "deleteSaved") + '" data-v="' + esc(it.kind === "preset" ? it.id : String(it.index)) +
      '" data-card-id="' + esc(it.id) + '" aria-label="' + (armed ? "Confirmar apagar: " : "Apagar: ") + esc(it.name) + '" title="' + (armed ? "Clique de novo para apagar" : "Apagar") + '">' + (armed ? "Apagar?" : icon("trash")) + "</button>";
    return '<div class="spec-row' + (selected ? " is-sel" : "") + '">' + main + side + "</div>";
  }

  // ---- seções do documento ------------------------------------------------------------
  function sectionHead(title, aside) {
    return '<div class="sec-head"><h2>' + title + "</h2>" + (aside || "") + "</div>";
  }

  function saveBadge() {
    // Só a falha aparece; salvar é automático e não precisa de aviso.
    if (S.saveState === "error") return '<span class="save bad" role="status" title="' + esc(S.saveError) + '">não salvo</span>';
    return "";
  }

  function sectionVisual() {
    var items = catalogItems();
    var mine = S.savedStyles.length;
    return '<section class="sec" id="sec-visual">' + sectionHead("Visual", '<button class="tlink" data-act="lib">Meus modelos' + (mine ? ' <span class="cnt">' + mine + "</span>" : "") + icon("chevRight") + "</button>") +
      '<div class="specs" data-gallery="1" role="group" aria-label="Estilos">' + items.map(function (it) { return specRow(it); }).join("") + "</div></section>";
  }

  function seg(st, field, options, label) {
    return '<div class="seg" role="group"' + (label ? ' aria-label="' + esc(label) + '"' : "") + ">" + options.map(function (o) {
      return '<button aria-pressed="' + (st[field] === o[0]) + '" data-act="styleSet" data-k="' + field + '" data-v="' + o[0] + '">' + o[1] + "</button>";
    }).join("") + "</div>";
  }

  function range(st, id, field, min, max, step, label, fmt, change) {
    var v = st[field] == null ? min : st[field];
    return '<label for="' + id + '" class="lbl">' + label + "</label>" +
      '<div class="rng"><input id="' + id + '" type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + v + '" data-change="' + (change || "styleNumber") + '" data-k="' + field + '"><output for="' + id + '">' + fmt(v) + "</output></div>";
  }

  function fontOptions(list, current, extraFirst) {
    var q = (S.fontQuery || "").trim().toLowerCase();
    var shown = list.filter(function (f) { return !q || f.toLowerCase().indexOf(q) >= 0 || f === current; });
    return (extraFirst || "") + shown.map(function (f) {
      return '<option style="font-family:\'' + esc(f) + '\'"' + (f === current ? " selected" : "") + ">" + esc(f) + "</option>";
    }).join("");
  }

  function toggle(st, field, label, ic) {
    return '<button class="tgl" aria-pressed="' + !!st[field] + '" data-act="styleSet" data-k="' + field + '" data-v="' + !st[field] + '" aria-label="' + label + '" title="' + label + '">' + ic + "</button>";
  }

  function swatch(st, key, label, shown) {
    var v = shown || st[key] || "#FFFFFF";
    return '<label class="sw"><input type="color" data-change="color" data-k="' + key + '" value="' + esc(v) + '" aria-label="Cor: ' + label + '"><i style="background:' + esc(v) + '"></i><span>' + label + "</span></label>";
  }

  /* Mesma regra do motor (StyleLayers.resolve / voice_target / with_visible_highlight):
     com destaque ligado, "Cor por voz: Texto" pinta o destaque; destaque parecido com o texto
     troca por uma cor que aparece. */
  function hexDist(a, b) {
    var x = /^#?([0-9a-f]{6})$/i.exec(a || ""), y = /^#?([0-9a-f]{6})$/i.exec(b || "");
    if (!x || !y) return 999;
    var d = 0;
    for (var i = 0; i < 6; i += 2) { var u = parseInt(x[1].substr(i, 2), 16) - parseInt(y[1].substr(i, 2), 16); d += u * u; }
    return Math.sqrt(d);
  }
  function voiceTarget(st) {
    var t = st.speaker_color_target || "none";
    return t === "text" && st.karaoke && st.karaoke !== "none" ? "highlight" : t;
  }
  function effectiveColors(st, sp) {
    var target = voiceTarget(st), txt = st.text_color || "#FFFFFF", hl = st.highlight_color || "#FFD400";
    if (sp && sp.color && target === "text") txt = sp.color;
    if (sp && sp.color && target === "highlight") hl = sp.color;
    if (st.karaoke && st.karaoke !== "none" && hexDist(txt, hl) < 90) hl = hexDist(txt, "#FFFFFF") < 90 ? "#FFD400" : "#FFFFFF";
    return { txt: txt, hl: hl, target: target };
  }
  // Mesma regra do motor (style.readable_on): contraste de leitura WCAG ≥ 3, senão quase preto ou branco.
  function lum(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return 1;
    return [0, 2, 4].map(function (i) { var v = parseInt(m[1].substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); })
      .reduce(function (s, v, i) { return s + v * [0.2126, 0.7152, 0.0722][i]; }, 0);
  }
  function contrastRatio(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function markedColor(color, marker) {
    if (contrastRatio(color, marker) >= 3) return color;
    return contrastRatio("#111111", marker) >= contrastRatio("#FFFFFF", marker) ? "#111111" : "#FFFFFF";
  }

  function voiceColors() {
    var inc = activeSpeakers().filter(function (sp) { return sp.include_in_captions !== false; });
    if (inc.length < 2) return "";
    return '<div class="vcolors">' + inc.map(function (sp) {
      return '<label class="sw"><input type="color" data-change="spcolor" data-v="' + esc(sp.id) + '" value="' + esc(sp.color) + '" aria-label="Cor de ' + esc(spName(sp)) + '"><i style="background:' + esc(sp.color) + '"></i><span>' + esc(spName(sp)) + "</span></label>";
    }).join("") + "</div>";
  }

  // Ajustes de animação: mexer neles cria um visual novo (nenhuma amostra fica marcada).
  var ANIM_KEYS = ["animation_in", "animation_ms", "text_mode", "karaoke", "marker", "marker_opacity", "active_scale", "wobble", "wobble_ms", "wobble_pct", "arch_pct",
    "entry_unit", "entry_stagger_pct", "entry_rise_pct", "entry_blur_px", "entry_scale", "entry_rotate_deg", "entry_fade",
    "entry_shift_pct", "entry_order", "entry_tint"];
  function customAnimation() {
    if (!S.style) return false;
    var o = S.style.overrides || {}, base = (catalogEntry(S.style.preset) || {}).spec || {};
    return ANIM_KEYS.some(function (k) { return o[k] !== undefined && o[k] !== base[k]; });
  }

  // Contorno: quatro espessuras prontas (px a 1080p); "Sem" tira o contorno.
  var OUTLINES = [[0, "Sem"], [2, "Fino"], [4, "Médio"], [7, "Grosso"]];
  function outlineLevel(px) {
    var best = OUTLINES[0];
    OUTLINES.forEach(function (o) { if (Math.abs(o[0] - px) < Math.abs(best[0] - px)) best = o; });
    return px > 0 && best[0] === 0 ? OUTLINES[1][0] : best[0];
  }
  // Torção de 0 a 100: ritmo (100 = mais rápido) e intensidade.
  function wobbleRate(ms) { return Math.round((600 - ms) / 540 * 100); }
  function wobbleMs(rate) { return Math.round(600 - rate / 100 * 540); }
  function wobbleAmount(pct) { return Math.round((pct - 0.3) / 3.7 * 100); }
  function wobblePct(amount) { return Math.round((0.3 + amount / 100 * 3.7) * 100) / 100; }
  function seqHeight() { var q = S.project && S.project.sequence; return (q && q.height) || 1080; }
  function nativeMode() { return S.mode === "native"; }

  function fontControls(st) {
    var fonts = S.fonts.slice();
    if (fonts.indexOf(st.font_family) < 0) fonts.unshift(st.font_family);
    var h = seqHeight(), px = Math.round(st.font_size_pct * h / 100);
    var native = nativeMode();
    var ec = effectiveColors(st, activeSpeakers().filter(function (sp) { return sp.include_in_captions !== false; })[0]);  // o que a 1ª voz mostra
    return '<div class="adj">' +
      '<div class="fontbox"><span class="aa" style="font-family:\'' + esc(st.font_family) + '\';font-weight:' + (st.bold ? 700 : 400) + ";font-style:" + (st.italic ? "italic" : "normal") + '" aria-hidden="true">Aa</span>' +
        '<div class="fontpick"><label class="sr" for="font">Fonte</label><select id="font" class="field fsel" data-change="font" style="font-family:\'' + esc(st.font_family) + '\'">' + fontOptions(fonts, st.font_family) + "</select>" +
        '<label class="search">' + icon("search") + '<span class="sr">Filtrar fontes</span><input id="fontSearch" type="search" data-input="fontQuery" placeholder="Buscar entre ' + fonts.length + ' fontes" value="' + esc(S.fontQuery || "") + '"></label></div></div>' +
      '<div class="line">' + toggle(st, "bold", "Negrito", "<b>B</b>") + toggle(st, "italic", "Itálico", "<i>I</i>") + toggle(st, "uppercase", "Maiúsculas", "AA") +
        '<label class="sizebox grow" title="Tamanho da fonte em pixels, na resolução da sequência (' + h + ' px de altura)"><span>Tamanho</span><input id="size" class="field num" type="number" inputmode="numeric" min="8" max="' + Math.round(h * 0.3) + '" step="1" value="' + px + '" data-change="sizePx"><span>px</span></label></div>' +
      '<div class="swatches">' + swatch(st, "text_color", "Texto", ec.txt) + swatch(st, "highlight_color", "Destaque", ec.hl) + swatch(st, "outline_color", "Contorno") + "</div>" +
      '<div class="form">' +
        '<span class="lbl">Contorno</span>' + seg({ outline_px: outlineLevel(st.outline_px) }, "outline_px", OUTLINES, "Espessura do contorno") +
        '<span class="lbl">Destaque</span>' + seg(st, "karaoke", [["none", "Sem"], ["active", "Palavra"], ["color", "Acumula"], ["fill", "Preenche"]], "Destaque da palavra falada") +
        '<span class="lbl">Linhas</span>' + seg(st, "max_lines", [[1, "1 linha"], [2, "2 linhas"]], "Linhas por legenda") +
      "</div>" +
      '<p class="hint">O que não couber nas linhas vira o próximo trecho da legenda.</p>' +
      (native ? '<p class="hint native-note">' + icon("warn") + "<span>Formato <b>Nativa</b>: o Premiere usa o estilo de legenda dele. Do Dynamic Captions vão só o texto, as linhas e o tempo; fonte, cores, posição e animações não são aplicados.</span></p>" : "") +
      '<button class="disclose" data-act="adv" aria-expanded="' + (S.adv && !native) + '"' + (native ? ' disabled title="Não se aplica à legenda nativa do Premiere"' : "") + ">" + icon(S.adv && !native ? "chevDown" : "chevRight") + "Animação e ajustes</button>" +
      (S.adv && !native ? animControls(st) + '<div class="form">' +
        '<span class="lbl">Cor por voz</span>' + seg(st, "speaker_color_target", [["text", "Texto"], ["highlight", "Destaque"], ["none", "Única"]], "Cor por voz") +
        "</div>" + voiceColors() + '<div class="form">' +
        range(st, "spacing", "spacing_px", 0, 12, 0.5, "Espaçamento", function (v) { return String(v).replace(".", ",") + " px"; }) +
        '<span class="lbl">Caixa</span><div class="line">' + seg(st, "box", [[false, "Não"], [true, "Sim"]], "Caixa de fundo") +
          '<label class="sw solo"><input id="bgcolor" type="color" data-change="color" data-k="box_color" value="' + esc(st.box_color || "#000000") + '" aria-label="Cor da caixa"><i style="background:' + esc(st.box_color || "#000000") + '"></i></label></div>' +
        (st.box ? range(st, "boxPad", "outline_px", 0, 20, 1, "Folga da caixa", function (v) { return v + " px"; }) : "") +
      "</div>" : "") +
      '<button class="tlink" data-act="goSave">' + icon("plus") + "Salvar como meu modelo</button>" +
      "</div>";
  }

  function animControls(st) {
    var pct = function (v) { return String(v).replace(".", ",") + "%"; };
    var temporal = st.text_mode === "word" || st.karaoke === "color" || st.karaoke === "fill" || st.animation_in === "typewriter";
    // Aberturas antigas (Desfoque, Inclinação) só aparecem se o projeto já usa uma delas.
    var motions = MOTIONS.filter(function (m) { return !m[2] || m[0] === st.animation_in; });
    var timed = st.animation_in !== "none";
    var ms = Math.max(MIN_MOTION_MS, st.animation_ms || 0);
    return '<div class="form">' +
      '<label class="lbl" for="motion">Abertura</label><select id="motion" class="field" data-change="styleValue" data-k="animation_in">' + motions.map(function (m) { return '<option value="' + m[0] + '"' + (st.animation_in === m[0] ? " selected" : "") + ">" + m[1] + "</option>"; }).join("") + "</select>" +
      (timed ? range({ animation_ms: ms }, "duration", "animation_ms", MIN_MOTION_MS, 1200, 20, "Duração", function (v) { return (v / 1000).toFixed(2).replace(".", ",") + " s"; }) : "") +
      (st.animation_in === "custom" ?
        '<span class="lbl">Animar por</span>' + seg(st, "entry_unit", [["phrase", "Frase"], ["word", "Palavra"], ["letter", "Letra"]], "Animar por frase, palavra ou letra") +
        (st.entry_unit !== "phrase" ? range(st, "entryStagger", "entry_stagger_pct", 0, 100, 5, "Atraso", function (v) { return v + "%"; }) : "") +
        (st.entry_unit !== "phrase" ? '<span class="lbl">Ordem</span>' + seg({ entry_order: st.entry_order || "forward" }, "entry_order", [["forward", "Normal"], ["reverse", "Inversa"], ["random", "Aleatória"]], "Ordem em que os itens entram") : "") +
        range(st, "entryRise", "entry_rise_pct", -15, 15, 0.5, "Posição vertical", function (v) { return v > 0 ? pct(v) + " abaixo" : v < 0 ? pct(-v) + " acima" : "0%"; }) +
        range({ entry_shift_pct: st.entry_shift_pct || 0 }, "entryShift", "entry_shift_pct", -15, 15, 0.5, "Posição horizontal", function (v) { return v > 0 ? pct(v) + " à direita" : v < 0 ? pct(-v) + " à esquerda" : "0%"; }) +
        range(st, "entryBlur", "entry_blur_px", 0, 40, 1, "Desfoque", function (v) { return v + " px"; }) +
        range(st, "entryScale", "entry_scale", 0.1, 2.5, 0.05, "Escala", function (v) { return Math.round(v * 100) + "%"; }) +
        '<span class="lbl">Fade</span>' + seg({ entry_fade: st.entry_fade !== false }, "entry_fade", [[false, "Não"], [true, "Sim"]], "Fade de abertura (opacidade de 0 a 100%)") +
        '<span class="lbl">Cor</span>' + seg({ entry_tint: !!st.entry_tint }, "entry_tint", [[false, "Do texto"], [true, "Do destaque"]], "Entra na cor de destaque e passa à cor do texto") +
        '<p class="hint span2">Valores no início da animação. Cada item termina em posição e escala normais.</p>'
        : "") +
      '<span class="lbl">Mostrar</span>' + seg(st, "text_mode", [["phrase", "Frase"], ["word", "Palavra"], ["progressive", "Revelar"]], "Exibição") +
      '<span class="lbl">Marca-texto</span><div class="line">' + seg(st, "marker", [["none", "Não"], ["active", "Sim"]], "Marca-texto") +
        (st.marker === "active" ? '<label class="sw solo"><input id="mkColor" type="color" data-change="color" data-k="marker_color" value="' + esc(st.marker_color || "#FFD400") + '" aria-label="Cor do marca-texto"><i style="background:' + esc(st.marker_color || "#FFD400") + '"></i></label>' : "") + "</div>" +
      (st.marker === "active" ? range(st, "mkOpacity", "marker_opacity", 0, 1, 0.05, "Opacidade", function (v) { return Math.round(v * 100) + "%"; }) : "") +
      range({ arch_pct: st.arch_pct || 0 }, "arch", "arch_pct", 0, 6, 0.5, "Arco", function (v) { return v ? pct(v) : "Sem"; }) +
      (st.arch_pct && st.wobble ? '<p class="hint span2">Com Torção ligada o arco não é aplicado.</p>' : "") +
      '<span class="lbl">Torção</span>' + seg({ wobble: !!st.wobble }, "wobble", [[false, "Não"], [true, "Sim"]], "Torção (letras ondulando, estilo desenho animado)") +
      (st.wobble ? range({ r: wobbleRate(st.wobble_ms) }, "wobbleRate", "r", 0, 100, 5, "Ritmo", String, "wobbleRate") +
        range({ a: wobbleAmount(st.wobble_pct) }, "wobbleAmount", "a", 0, 100, 5, "Intensidade", String, "wobbleAmount") : "") +
      "</div>" + (temporal ? '<p class="hint">Palavra, Acumula, Preenche e Digitação desligam o marca-texto.</p>' : "");
  }

  // Posicionamento: onde a legenda fica no quadro. "Voltar ao padrão" desfaz tudo deste bloco.
  function sectionPosicao() {
    var st = resolved();
    if (nativeMode()) return "";
    var pct = function (v) { return String(v).replace(".", ",") + "%"; };
    var o = (S.style && S.style.overrides) || {};
    var changed = PLACE_ONLY.some(function (k) { return o[k] !== undefined; });
    return '<section class="sec" id="sec-posicao">' + sectionHead("Posicionamento",
        '<button class="tlink" data-act="resetPlace" ' + (changed ? "" : "disabled") + ' title="Voltar à posição do visual escolhido">' + icon("undo") + "Voltar ao padrão</button>") +
      '<div class="adj"><div class="form">' +
        '<span class="lbl">Posição</span>' + seg(st, "position", [["top", "Topo"], ["middle", "Meio"], ["bottom", "Rodapé"]], "Posição") +
        range(st, "offX", "offset_x_pct", -50, 50, 0.5, "Horizontal", pct) +
        range(st, "offY", "offset_y_pct", -50, 50, 0.5, "Vertical", pct) +
        (st.position !== "middle" ? range(st, "mg", "margin_pct", 0, 30, 1, "Margem", pct, "margin") : "") +
      "</div></div></section>";
  }

  function sectionAjustes() {
    var st = resolved();
    var warn = (S.style.font_warnings || []).length ? '<p class="alert warn">' + icon("warn") + "<span>" + S.style.font_warnings.map(esc).join("<br>") + "</span></p>" : "";
    return '<section class="sec" id="sec-ajustes">' + sectionHead("Fonte", saveBadge()) + warn + fontControls(st) + "</section>";
  }

  function queueBanner() {
    var d = openItem();
    if (!d) return "";
    var primary = d.kind === "verifier_only" ? "(nada)" : d.primary_text;
    var alt = d.alternative_text || "(remover)";
    var why = d.kind === "substitution" ? "dois modelos ouviram diferente" : d.kind === "primary_only" ? (d.suspected_hallucination ? "pode ser texto inventado" : "só um modelo ouviu") : "só o segundo modelo ouviu";
    return '<div class="queue" role="group" aria-label="Palavra para conferir"><div class="q-head"><b>' + S.review.length + (S.review.length === 1 ? " trecho para conferir" : " trechos para conferir") + '</b><span>' + why + " · " + tc(offset() + d.start_us) + '</span><button class="ib sm" aria-label="Ouvir o trecho" data-act="listenItem">' + icon("play") + "</button></div>" +
      '<div class="q-opts"><button class="opt" data-act="resolve" data-v="keep"><small>Manter</small>' + esc(primary) + '</button><button class="opt" data-act="resolve" data-v="alt"><small>' + (d.kind === "verifier_only" ? "Inserir" : "Trocar por") + "</small>" + esc(alt) + "</button></div></div>";
  }

  function wordCard(w) {
    var sp = speaker(w.speaker_id);
    var opts = S.transcript.speakers.map(function (s) { return '<option value="' + esc(s.id) + '"' + (s.id === w.speaker_id ? " selected" : "") + ">" + esc(spName(s)) + "</option>"; }).join("") + '<option value="__new">+ nova voz</option>';
    var p = wordStyle(w.id), st = resolved(), b = blockOf(w.id);
    var fonts = S.fonts.slice();
    if (p.font_family && fonts.indexOf(p.font_family) < 0) fonts.unshift(p.font_family);
    var note = b ? "" : "Esta palavra não está nas legendas (voz fora das legendas?). O ajuste fica guardado.";
    function tri(field, label) {
      return '<span class="lbl">' + label + '</span><div class="seg" role="group" aria-label="' + label + '">' + [[null, "Do estilo"], [true, "Sim"], [false, "Não"]].map(function (o) {
        return '<button aria-pressed="' + (p[field] === o[0]) + '" data-act="wordSet" data-k="' + field + '" data-v="' + o[0] + '">' + o[1] + "</button>";
      }).join("") + "</div>";
    }
    var styleOpen = S.wordPanel == null ? hasWordStyle(w.id) : S.wordPanel;
    return '<div class="wcard word-card" role="group" aria-label="Palavra selecionada">' +
      '<div class="wc-top"><label class="sr" for="corr">Corrigir palavra</label><input id="corr" class="field" data-input="draft" maxlength="60" value="' + esc(S.draft) + '">' +
        '<button class="btn dark" data-act="saveWord">Corrigir</button>' +
        '<button class="ib" aria-label="Ouvir trecho" data-act="listenWord">' + icon("play") + '</button><button class="ib" aria-label="Mostrar na sequência" data-act="seekWord" ' + (host.canPremiere ? "" : "disabled") + ">" + icon("target") + "</button>" +
        '<button class="ib" aria-label="Fechar" data-act="unpick">' + icon("x") + "</button></div>" +
      '<div class="wc-meta"><label class="sr" for="spk">Voz</label><select id="spk" class="field mini" data-change="setSpeaker">' + opts + '</select><span class="tc">' + tc(offset() + w.start_us) + "</span><span>" + esc(S.flash || (w.locked ? "corrigida por você" : w.timing === "aligned" ? "automática · alinhada" : "automática")) + "</span></div>" +
      '<button class="disclose" data-act="wordPanel" aria-expanded="' + styleOpen + '">' + icon(styleOpen ? "chevDown" : "chevRight") + "Estilo só desta palavra" + (hasWordStyle(w.id) ? ' <span class="badge">personalizada</span>' : "") + "</button>" +
      (styleOpen ? '<div class="form">' +
        '<label class="lbl" for="wscales">Tamanho</label><div class="rng"><input id="wscales" type="range" min="0.5" max="2.5" step="0.05" value="' + p.font_scale + '" data-change="wordNumber" data-k="font_scale"><output for="wscales">' + p.font_scale.toFixed(2).replace(".", ",") + "×</output></div>" +
        '<label class="lbl" for="wfonts">Fonte</label><select id="wfonts" class="field" data-change="wordFont"><option value="">Do estilo (' + esc(st.font_family) + ")</option>" +
          fonts.map(function (f) { return '<option style="font-family:\'' + esc(f) + '\'"' + (f === p.font_family ? " selected" : "") + ">" + esc(f) + "</option>"; }).join("") + "</select>" +
        tri("bold", "Negrito") + tri("italic", "Itálico") +
        '<span class="lbl">Cor</span><div class="line"><label class="check"><input type="checkbox" data-change="wordColorOn" ' + (p.text_color ? "checked" : "") + "><span>Cor própria</span></label>" +
          '<label class="sw solo"><input type="color" aria-label="Cor desta palavra" data-change="wordColor" value="' + esc(p.text_color || st.text_color || "#FFFFFF") + '" ' + (p.text_color ? "" : "disabled") + '><i style="background:' + esc(p.text_color || st.text_color || "#FFFFFF") + '"></i></label></div>' +
        '<span class="lbl">Linha</span><label class="check"><input type="checkbox" data-change="wordBreak" ' + (p.break_before ? "checked" : "") + "><span>Quebrar linha antes</span></label>" +
        "</div>" + (note ? '<p class="hint">' + note + "</p>" : "") +
        '<button class="tlink" data-act="wordReset" ' + (hasWordStyle(w.id) ? "" : "disabled") + ">" + icon("restore") + "Voltar ao estilo</button>" : "") +
      "</div>";
  }

  function sectionFala() {
    var dis = disputedSet(), list = activeSpeakers();
    var tUs = S.blocks.length ? previewTimeUs() : -1, pb = S.blocks.length ? previewBlock(tUs) : null;
    var live = {}; blockWordIds(pb).forEach(function (id) { live[id] = true; });
    var paras = segments().map(function (g) {
      var sp = speaker(g.speaker);
      var color = sp ? sp.color : "#888";
      var excluded = sp && sp.include_in_captions === false;
      var hasSel = g.words.some(function (w) { return w.id === S.sel; });
      return '<article class="para' + (excluded ? " excluded" : "") + '"><div class="who"><i style="background:' + esc(color) + '"></i><b>' + esc(spName(sp)) + '</b><span class="tc">' + tc(offset() + g.words[0].start_us) + "</span>" + (excluded ? '<span class="tag">fora das legendas</span>' : "") + "</div>" +
        '<p class="words">' + g.words.map(function (w) {
          var cls = "w" + (dis[w.id] ? " disputed" : "") + (w.locked ? " locked" : "") + (S.sel === w.id ? " sel" : "") + (live[w.id] ? " live" : "") + ((w.flags || []).indexOf("hallucination_suspect") >= 0 ? " suspect" : "") + (hasWordStyle(w.id) ? " styled" : "");
          if (S.inlineEdit === w.id) {
            // Dois cliques: a palavra vira um campo ali mesmo. Enter salva, Esc cancela.
            return '<input id="inlineEdit" class="w-edit" data-v="' + esc(w.id) + '" aria-label="Corrigir “' + esc(w.text) + '”" value="' + esc(S.inlineDraft) + '" size="' + Math.min(28, Math.max(2, S.inlineDraft.length + 1)) + '" maxlength="60" spellcheck="false">';
          }
          return '<button class="' + cls + '" data-act="pick" data-v="' + esc(w.id) + '" title="Dois cliques para corrigir">' + esc(w.text) + "</button>";
        }).join(" ") + "</p>" + (hasSel ? wordCard(wordById(S.sel)) : "") + "</article>";
    }).join("");
    var warnings = (S.warnings || []).length ? '<p class="alert warn">' + icon("warn") + "<span>" + S.warnings.map(esc).join("<br>") + "</span></p>" : "";
    return '<section class="sec" id="sec-fala">' + sectionHead("Fala", '<button class="tlink" data-act="voices">' + icon("voices") + (list.length === 1 ? "1 voz" : list.length + " vozes") + icon("chevRight") + "</button>") +
      warnings + queueBanner() + '<div class="doc" aria-label="Transcrição">' + paras + "</div></section>";
  }

  // ---- telas --------------------------------------------------------------------------
  function screenDoc() {
    if (!S.transcript || !S.style) return "";
    if (wide()) {
      // Lateral primeiro no DOM: Tab chega a estilos e ajustes antes das palavras do documento.
      return '<div class="ws"><div class="col-side">' + sectionVisual() + sectionAjustes() + sectionPosicao() + '</div><div class="col-main">' + previewCard() + sectionFala() + "</div></div>";
    }
    return previewCard() + sectionVisual() + sectionAjustes() + sectionPosicao() + sectionFala();
  }

  function sheetHead(title, aside) {
    return '<div class="sheet-head"><button class="ib" data-act="doc" aria-label="Voltar">' + icon("back") + "</button><h2>" + title + "</h2>" + (aside || "") + "</div>";
  }

  function personalBar() {
    var report = S.importReport ? '<div class="alert ' + (S.importReport.added ? "ok" : "warn") + '" role="status"><span><b>' + S.importReport.added + ' estilo(s) importado(s).</b>' +
      (S.importReport.problems.length ? "<br>" + S.importReport.problems.map(esc).join("<br>") : "") + '</span><button class="ib sm" data-act="closeImport" aria-label="Fechar aviso">' + icon("x") + "</button></div>" : "";
    return '<div class="personal"><div class="line"><label class="sr" for="presetName">Nome do meu modelo</label><input id="presetName" class="field grow" data-input="presetName" value="' + esc(S.presetName) + '" placeholder="Nome para o estilo atual">' +
      '<button class="btn dark" data-act="savePreset" ' + (S.savingStyle ? "disabled" : "") + ">Salvar</button></div>" +
      '<div class="line"><button class="tlink" data-act="importSaved">' + icon("upload") + 'Importar .json</button><button class="tlink" data-act="exportSaved" ' + (S.savedStyles.length ? "" : "disabled") + ">" + icon("download") + "Exportar .json</button>" +
      '<input type="file" id="importFile" class="sr" tabindex="-1" aria-hidden="true" accept=".json,application/json" data-change="importFile"></div>' + report + "</div>";
  }

  function screenLib() {
    var list = savedItems().concat(catalogItems());
    var rover = list.some(function (it) { return it.id === S.focusCard; }) ? S.focusCard : (list[0] || {}).id;
    return '<div class="sheet lib">' + sheetHead("Meus modelos", saveBadge()) +
      '<p class="lead">Guarde o visual atual com um nome para reaplicar em outros vídeos. Os visuais do plugin também podem ser apagados.</p>' +
      personalBar() +
      '<div class="specs all" data-gallery="1" role="group" aria-label="' + list.length + (list.length === 1 ? " modelo" : " modelos") + '">' +
      (list.map(function (it) { return specRow(it, { tools: true, rover: rover }); }).join("") || '<p class="empty">Nenhum visual. Restaure os do plugin ou salve um novo.</p>') + "</div>" +
      (S.hiddenPresets.length ? '<button class="tlink" data-act="restorePresets">' + icon("refresh") + "Restaurar os visuais do plugin (" + S.hiddenPresets.length + ")</button>" : "") + "</div>";
  }

  function speakerCountSelect() {
    return '<label class="lbl" for="voiceCount">Vozes</label><select id="voiceCount" class="field" data-change="speakerCount"><option value="auto">Detectar sozinho</option>' +
      [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16].map(function (n) {
        return '<option value="' + n + '"' + (String(n) === S.speakerCount ? " selected" : "") + '>' + n + (n > 1 ? " vozes" : " voz") + "</option>";
      }).join("") + "</select>";
  }

  function screenVoices() {
    var counts = {};
    S.transcript.words.forEach(function (w) { counts[w.speaker_id] = (counts[w.speaker_id] || 0) + 1; });
    var list = activeSpeakers();
    return '<div class="sheet voices">' + sheetHead("Vozes") +
      '<p class="lead">Dê nomes e cores. Desmarque quem não deve aparecer nas legendas; a fala continua no documento.</p>' +
      '<div class="vlist">' + list.map(function (sp) {
        return '<div class="vrow' + (sp.include_in_captions === false ? " off" : "") + '">' +
          '<label class="sw solo big"><input type="color" data-change="spcolor" data-v="' + esc(sp.id) + '" value="' + esc(sp.color) + '" aria-label="Cor de ' + esc(spName(sp)) + '"><i style="background:' + esc(sp.color) + '"></i></label>' +
          '<div class="vmain"><input class="field" aria-label="Nome da voz ' + esc(sp.label) + '" data-change="spname" data-v="' + esc(sp.id) + '" value="' + esc(spName(sp)) + '"><span class="count">' + (counts[sp.id] || 0) + " palavras</span></div>" +
          '<button class="ib" data-act="listenSpeaker" data-v="' + esc(sp.id) + '" aria-label="Ouvir ' + esc(spName(sp)) + '">' + icon("volume") + "</button>" +
          '<label class="switch"><input type="checkbox" data-change="speakerIncluded" data-v="' + esc(sp.id) + '" ' + (sp.include_in_captions !== false ? "checked" : "") + '><span class="track"></span><span class="sr">Legendar</span></label></div>';
      }).join("") + "</div>" +
      (list.length === 1 ? '<p class="alert warn">' + icon("warn") + "<span>Só uma voz foi encontrada. Se o trecho tem outras falas, confira de novo.</span></p>" : "") +
      '<div class="block"><h3>Conferir as vozes de novo</h3><div class="form">' + speakerCountSelect() + "</div>" +
      '<div class="line"><button class="btn" data-act="rediarize" ' + (S.busy ? "disabled" : "") + ">" + icon("refresh") + (S.busy === "speakers" ? "Conferindo…" : "Conferir vozes") + "</button>" +
      (S.busy === "speakers" && S.job ? '<button class="tlink" data-act="cancel">Cancelar</button>' : "") + "</div>" +
      '<p class="hint">Mantém texto, tempos e correções. Em trechos curtos usa 30 s de áudio em volta.</p></div></div>';
  }

  function screenInicio() {
    if (!host.canPremiere) {
      return '<div class="hero"><h2>Fora do Premiere</h2><p>Esta janela abre projetos já legendados. Para legendar e aplicar, use o painel dentro do Premiere.</p></div>';
    }
    if (!S.seq) {
      if (S.seqError) return '<div class="hero"><h2>Não consegui ler o Premiere</h2><p>' + esc(S.seqError) + '</p><button class="btn" data-act="refresh">' + icon("refresh") + "Tentar de novo</button></div>";
      return '<div class="hero"><h2>Abra uma sequência</h2><p>O Dynamic Captions legenda a sequência ativa na Timeline.</p><button class="btn" data-act="refresh">' + icon("refresh") + "Ler de novo</button></div>";
    }
    var seq = S.seq, hasIO = seq.in_us != null;
    function choice(id, title, meta, disabled) {
      return '<button class="pick" role="radio" aria-checked="' + (S.scope === id) + '" aria-pressed="' + (S.scope === id) + '" data-act="scope" data-v="' + id + '" ' + (disabled ? "disabled" : "") + '><span class="dot"></span><span><b>' + title + '</b><span class="tc">' + meta + "</span></span></button>";
    }
    var e = engineState();
    return '<div class="start">' +
      '<h2 class="big">Legendar</h2>' +
      '<div class="picks" role="radiogroup" aria-label="O que legendar">' +
        choice("inout", "Trecho In/Out", hasIO ? tc(seq.in_us) + " – " + tc(seq.out_us) : "marque In e Out na Timeline", !hasIO) +
        choice("seq", "Sequência inteira", tc(0) + " – " + tc(seq.end_us), false) + "</div>" +
      '<div class="form">' + speakerCountSelect() +
        '<label class="lbl" for="dic">Nomes</label><input id="dic" class="field" data-input="dictionary" value="' + esc(S.dictionary) + '" placeholder="Ana, Bruno, Premiere (opcional)"></div>' +
      (windowsBlocked() ? '<div class="notice err" role="alert"><b>O Windows está bloqueando o motor de transcrição.</b>' +
        "<p>O Controle Inteligente de Aplicativos recusou arquivos do PyTorch que não têm assinatura digital, então não dá para legendar neste computador por enquanto. " +
        "Os seus projetos e as legendas já aplicadas não são afetados.</p>" +
        "<p>Para liberar: Segurança do Windows › Controle de aplicativos e do navegador › Controle Inteligente de Aplicativos › Desativado. Depois feche e abra o Premiere.</p></div>" : "") +
      '<button class="btn dark big" data-act="legendar" ' + (S.busy || windowsBlocked() ? "disabled" : "") + ">" + (S.scope === "inout" ? "Legendar trecho" : "Legendar sequência") + icon("chevRight") + "</button>" +
      (e[0] === "wait" ? '<p class="hint">Os modelos estão carregando; a legenda começa assim que terminarem.</p>' : "") +
      (S.backPid ? '<button class="tlink" data-act="backDoc">' + icon("back") + "Voltar às legendas</button>" : "") +
      "</div>";
  }

  function screenProc() {
    var cur = S.job ? S.job.stage : "export";
    var idx = STAGES.findIndex(function (s) { return s[0] === cur; });
    if (idx < 0) idx = cur === "audio" ? 0 : 1;
    var pct = Math.round(((S.job && S.job.progress) || 0) * 100);
    var stages = STAGES.filter(function (s) { return s[0] !== "verify"; });
    var at = stages.findIndex(function (s) { return s[0] === cur; }); if (at < 0) at = idx;
    return '<div class="proc"><div class="proc-head"><h2>' + esc(stages[Math.min(at, stages.length - 1)][1]) + '…</h2><span class="tc">' + pct + "%</span></div>" +
      '<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"><i style="width:' + pct + '%"></i></div>' +
      '<ol class="steps">' + stages.map(function (s, i) { return '<li class="' + (i < at ? "done" : i === at ? "now" : "") + '">' + (i < at ? icon("check") : "<i></i>") + s[1] + "</li>"; }).join("") + "</ol>" +
      '<div class="ghost-doc" aria-hidden="true"><i></i><i></i><i class="s"></i><i></i><i class="s"></i></div>' +
      '<button class="btn" data-act="cancel" ' + (S.cancelRequested ? "disabled" : "") + ">" + (S.cancelRequested ? "Cancelando…" : "Cancelar") + "</button></div>";
  }

  function screenMsg(kind, title, text, actions) {
    return '<div class="hero ' + kind + '"><h2>' + title + "</h2><p>" + text + '</p><div class="line">' + actions + "</div></div>";
  }

  function footer() {
    if (S.screen !== "doc" || !S.transcript) return "";
    var applied = S.project && S.project.applied, dirty = S.project && S.project.dirty_since_apply;
    var info = S.blocks.length + (S.blocks.length === 1 ? " legenda" : " legendas");
    var sub = "";
    var r = S.applyResult;
    if (S.busy === "apply" && S.job) {
      var p = Math.round((S.job.progress || 0) * 100);
      var label = S.job.stage === "place" ? "Colocando na sequência…" : S.cancelRequested ? "Cancelando…" : "Renderizando " + p + "%";
      return '<footer class="act busy" role="status"><div class="sum"><b>' + label + '</b><div class="bar"><i style="width:' + p + '%"></i></div></div>' +
        (S.job.stage !== "place" ? '<button class="btn" data-act="cancelApply" ' + (S.cancelRequested || !S.job.id ? "disabled" : "") + ">Cancelar</button>" : "") + "</footer>";
    }
    if (S.saveState === "error") sub = '<span class="err">Estilo não salvo: ' + esc(S.saveError) + "</span>";
    else if (r && r.error) sub = '<span class="err">Não aplicou: ' + esc(r.error) + "</span>";
    else if (applied && dirty) sub = '<span class="warn">Mudou depois de aplicar</span>';
    else if (r && r.cancelled) sub = "Cancelado; nada mudou na sequência.";
    else if (r && r.mode === "animated") sub = '<span class="ok">' + icon("check") + "Na V" + r.track + " · " + r.placed + " novas, " + r.kept + " mantidas" + (r.seconds != null ? " · " + String(r.seconds.toFixed(1)).replace(".", ",") + " s" : "") + "</span>";
    else if (r && r.mode === "native") sub = '<span class="ok">' + icon("check") + "Faixa de legendas criada</span>";
    else if (S.mode === "native") sub = "Atualizar cria outra faixa de legendas.";
    else if (!host.canPremiere) sub = "Aplicar funciona dentro do Premiere.";
    else sub = activeSpeakers().length + (activeSpeakers().length === 1 ? " voz" : " vozes") + (S.review.length ? " · " + S.review.length + " para conferir" : "");
    var same = applied && !dirty && applied.mode === S.mode;
    var label2 = S.savingStyle ? "Salvando…" : !applied ? "Aplicar na sequência" : same ? "Aplicado" : "Atualizar na sequência";
    var disabled = S.busy || S.savingStyle || S.saveState === "error" || !S.blocks.length || !host.canPremiere || same;
    return '<footer class="act"><div class="sum"><b>' + info + "</b><span>" + sub + "</span></div>" +
      '<label class="sr" for="mode">Formato</label><select id="mode" class="field mode" data-change="mode"><option value="animated"' + (S.mode === "animated" ? " selected" : "") + '>Animada</option><option value="native"' + (S.mode === "native" ? " selected" : "") + ">Nativa</option></select>" +
      '<button class="btn dark go" data-act="apply" ' + (disabled ? "disabled" : "") + ">" + (same ? icon("check") : "") + label2 + "</button></footer>";
  }

  function body() {
    if (S.phase === "connecting" && S.status === WAIT_USE) return '<div class="hero"><h2>Dynamic Captions pronto para iniciar</h2>' +
      '<p class="hint">Ele não carrega nada enquanto o Premiere abre, para não atrasar a abertura.</p>' +
      '<button class="btn dark" data-act="start">Iniciar o Dynamic Captions</button></div>';
    if (S.phase === "connecting") return '<div class="hero"><h2>' + esc(S.status) + '</h2><div class="bar indet"><i></i></div></div>';
    if (S.phase === "setup") return screenMsg("warn", "Preparação inicial", "O painel não encontrou o motor do Dynamic Captions neste computador. Rode o instalador do Dynamic Captions de novo (ou, no Menu Iniciar, Dynamic Captions › Verificar instalação). Depois feche e abra o painel.", '<button class="btn" data-act="retry">Tentar de novo</button>');
    if (S.phase === "token") return screenMsg("warn", "Abra pelo painel", "Esta janela é aberta pelo menu do painel no Premiere (Abrir em janela grande).", "");
    if (S.phase === "motorOff") return screenMsg("err", "O Dynamic Captions não respondeu", "O serviço de transcrição deste computador não iniciou. Sua edição não é afetada.<br><small>" + esc(S.error) + "</small>", '<button class="btn dark" data-act="retry">Tentar de novo</button>');
    var s = S.screen;
    if (s === "inicio") return screenInicio();
    if (s === "proc") return screenProc();
    if (s === "cancel") return screenMsg("", "Cancelado", "Nada mudou na sequência.", '<button class="btn dark" data-act="legendar">Recomeçar</button><button class="btn" data-act="home">Voltar</button>');
    if (s === "semFala") return screenMsg("", "Nenhuma fala neste trecho", "Só música ou ambiente. Nenhuma legenda foi criada, em vez de inventar texto.", '<button class="btn dark" data-act="home">Escolher outro trecho</button>');
    if (s === "erro") return screenMsg("err", "Não deu certo", esc(friendly(S.error)) + "<br><small>" + esc(S.error) + "</small>", '<button class="btn dark" data-act="legendar">Tentar de novo</button><button class="btn" data-act="home">Voltar</button>');
    if (s === "lib" && S.style) return screenLib();
    if (s === "voices" && S.transcript) return screenVoices();
    if (s === "space") return screenSpace();
    return screenDoc();
  }

  function friendly(msg) {
    msg = String(msg || "");
    if (/preset/i.test(msg)) return "O preset de áudio do Premiere não foi encontrado.";
    if (/offline|não gravou o áudio/i.test(msg)) return "O Premiere não conseguiu ler o áudio da sequência (mídia offline?).";
    if (/pip install|falta o pacote/i.test(msg)) return "Falta instalar parte do motor neste computador.";
    if (/CUDA|memory/i.test(msg)) return "A placa de vídeo ficou sem memória ou indisponível.";
    return "Algo falhou no processamento. O resto do projeto não foi alterado.";
  }

  function render() {
    var ae = document.activeElement, active = ae && ae.id;
    // Foco por teclado sobrevive à reconstrução: id, amostra da biblioteca ou ação + valor.
    var activeSel = !active && ae && ae !== document.body && ae.getAttribute ?
      (ae.getAttribute("data-card") ? '[data-card="' + ae.getAttribute("data-card") + '"]' :
       ae.getAttribute("data-act") ? '[data-act="' + ae.getAttribute("data-act") + '"]' + (ae.getAttribute("data-v") != null ? '[data-v="' + ae.getAttribute("data-v") + '"]' : "") +
         (ae.getAttribute("data-k") ? '[data-k="' + ae.getAttribute("data-k") + '"]' : "") : null) : null;
    var scrollers = Array.prototype.map.call($app.querySelectorAll(".body, .col-main, .col-side"), function (p) { return p.scrollTop; });
    var sameScreen = render.last === S.screen + (wide() ? "/w" : "");
    var oldFrame = $app.querySelector(".frame");
    $app.className = "scr-" + S.screen + (wide() ? " is-wide" : "");
    var banner = S.banner ? '<div class="notice err" role="alert"><b>' + esc(S.banner.title) + "</b><p>" + esc(S.banner.text) + "</p></div>" : "";
    $app.innerHTML = header() + '<main class="body">' + banner + body() + "</main>" + footer() +
      (S.toast ? '<div class="toast ' + (S.toast.kind === "err" ? "err" : "good") + (S.toast.seen ? "" : " enter") + '" role="' + (S.toast.kind === "err" ? "alert" : "status") + '">' + esc(S.toast.msg) + "</div>" : "");
    if (S.toast) S.toast.seen = true;  // o aviso entra uma vez; redesenhos seguintes não repetem a animação
    // O aviso fica logo acima do rodapé (Aplicar) ou do fim do painel, sem cobrir o botão principal.
    var tEl = $app.querySelector(".toast");
    if (tEl) { var actEl = $app.querySelector(".act"); tEl.style.bottom = (actEl ? actEl.offsetHeight + 8 : 12) + "px"; }
    reuseFrame(oldFrame);
    measureFrame();
    if (S.animCard) { var keepStep = cardStep; cardStep = Math.max(1, keepStep); paintCard(); cardStep = keepStep; }
    if (sameScreen) Array.prototype.forEach.call($app.querySelectorAll(".body, .col-main, .col-side"), function (p, i) { if (scrollers[i]) p.scrollTop = scrollers[i]; });
    render.last = S.screen + (wide() ? "/w" : "");
    if (active) { var el = document.getElementById(active); if (el) { el.focus({ preventScroll: true }); if (el.setSelectionRange && el.value && /^(text|search)$/.test(el.type)) { try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* campo sem seleção */ } } } }
    else if (activeSel) { try { var again = $app.querySelector(activeSel.replace(/"([^"]*)"/g, function (_, v) { return '"' + v.replace(/["\\]/g, "\\$&") + '"'; })); if (again) again.focus({ preventScroll: true }); } catch (e) { /* seletor inválido */ } }
  }

  // ------------------------------------------------------------------ eventos
  function pickWord(v) {
    var w = wordById(v); if (!w) return;
    if (S.sel !== v && S.wordPanel === false) S.wordPanel = null;  // aberto continua aberto na próxima palavra
    S.sel = v; S.draft = w.text; S.flash = "";
    if (S.screen !== "doc") S.screen = "doc";
  }

  // ---- arrastar a legenda no quadro: move na hora (CSS) e salva o deslocamento ao soltar.
  var drag = null, suppressClick = false, lastDown = null;
  function round1(v) { return Math.round(v * 10) / 10; }
  function dragTo(nx, ny) {
    // O texto inteiro fica dentro do quadro (limites medidos ao começar a arrastar).
    var L = drag.lim || { x0: -50, x1: 50, y0: -50, y1: 50 };
    nx = Math.max(L.x0, Math.min(L.x1, nx)); ny = Math.max(L.y0, Math.min(L.y1, ny));
    if (Math.abs(nx) < 1.5) nx = 0;   // ímã no centro horizontal
    if (Math.abs(ny) < 1.5) ny = 0;   // ímã na posição do modelo
    drag.nx = nx; drag.ny = ny;
    drag.layer.style.transform = "translate(" + nx + "%," + ny + "%)";
    drag.frame.classList.toggle("snap-x", nx === 0);
    drag.frame.classList.toggle("snap-y", ny === 0);
  }
  function dragLimits(layer, fr, dx, dy) {
    var parts = layer.querySelectorAll(".caption-motion, .cap > *"), box = null;
    Array.prototype.forEach.call(parts, function (el) {
      var c = el.getBoundingClientRect(); if (!c.width || !c.height) return;
      box = box ? { l: Math.min(box.l, c.left), t: Math.min(box.t, c.top), r: Math.max(box.r, c.right), b: Math.max(box.b, c.bottom) } : { l: c.left, t: c.top, r: c.right, b: c.bottom };
    });
    if (!box || !fr.width || !fr.height) return null;
    // Posição do texto sem deslocamento, em % do quadro.
    var l = (box.l - fr.left) / fr.width * 100 - dx, rr = (box.r - fr.left) / fr.width * 100 - dx;
    var t = (box.t - fr.top) / fr.height * 100 - dy, bb = (box.b - fr.top) / fr.height * 100 - dy;
    function span(lo, hi) { return lo <= hi ? [lo, hi] : [(lo + hi) / 2, (lo + hi) / 2]; }
    var x = span(-l, 100 - rr), y = span(-t, 100 - bb);
    return { x0: Math.max(-50, x[0]), x1: Math.min(50, x[1]), y0: Math.max(-50, y[0]), y1: Math.min(50, y[1]) };
  }
  $app.addEventListener("pointerdown", function (ev) {
    var layer = ev.target.closest && ev.target.closest('[data-drag="caption"]');
    if (!layer || ev.button !== 0) return;
    // Dois toques centralizam. Detectado aqui porque o 1º toque escolhe a palavra e redesenha o quadro.
    var now = Date.now();
    if (lastDown && now - lastDown.t < 400 && Math.abs(ev.clientX - lastDown.x) + Math.abs(ev.clientY - lastDown.y) < 8) {
      lastDown = null; drag = null; suppressClick = true; setTimeout(function () { suppressClick = false; }, 500);
      var cur = resolved();
      if (cur.offset_x_pct || cur.offset_y_pct) setStyle({ offset_x_pct: 0, offset_y_pct: 0 });
      return;
    }
    lastDown = { t: now, x: ev.clientX, y: ev.clientY };
    var frame = layer.parentElement, r = frame.getBoundingClientRect(), st = resolved();
    drag = { layer: layer, frame: frame, w: r.width, h: r.height, x0: ev.clientX, y0: ev.clientY,
             dx: st.offset_x_pct || 0, dy: st.offset_y_pct || 0, nx: st.offset_x_pct || 0, ny: st.offset_y_pct || 0, moved: false, id: ev.pointerId };
    drag.lim = dragLimits(layer, r, drag.dx, drag.dy);
  });
  $app.addEventListener("pointermove", function (ev) {
    if (!drag) return;
    var mx = ev.clientX - drag.x0, my = ev.clientY - drag.y0;
    if (!drag.moved && Math.abs(mx) + Math.abs(my) < 4) return;
    if (!drag.moved) {
      drag.moved = true; drag.frame.classList.add("dragging");
      try { drag.layer.setPointerCapture(drag.id); } catch (e) { /* sem captura */ }
    }
    dragTo(drag.dx + mx / drag.w * 100, drag.dy + my / drag.h * 100);
  });
  function endDrag() {
    if (!drag) return;
    var d = drag; drag = null;
    d.frame.classList.remove("dragging", "snap-x", "snap-y");
    if (d.moved) {
      suppressClick = true; setTimeout(function () { suppressClick = false; }, 0);
      setStyle({ offset_x_pct: round1(d.nx), offset_y_pct: round1(d.ny) });
    }
  }
  $app.addEventListener("pointerup", endDrag);
  $app.addEventListener("pointercancel", endDrag);

  var lastPick = null;
  function startInline(id) {
    var w = wordById(id); if (!w) return;
    pickWord(id); S.inlineEdit = id; S.inlineDraft = w.text; render();
    var el = document.getElementById("inlineEdit"); if (el) { el.focus(); el.select(); }
  }
  function endInline(save) {
    var id = S.inlineEdit; if (!id) return;
    var text = (S.inlineDraft || "").trim(), w = wordById(id);
    S.inlineEdit = null;
    render();  // a palavra volta a ser texto na hora; a correção segue para o motor
    if (save && w && text && text !== w.text) { S.draft = text; ops([{ kind: "replace_text", word_id: id, text: text }], "Correção salva e travada."); }
    var b = $app.querySelector('.w[data-v="' + id + '"]'); if (b) b.focus({ preventScroll: true });
  }

  function focusSoon(selector) { var el = $app.querySelector(selector); if (el) el.focus(); }

  $app.addEventListener("click", function (ev) {
    if (suppressClick) { suppressClick = false; ev.preventDefault(); return; }
    var t = ev.target.closest("[data-act]");
    if (S.menu && (!t || t.getAttribute("data-act") !== "menu")) { S.menu = false; if (!t) { render(); return; } }
    if (!t || t.disabled) return;
    var a = t.getAttribute("data-act"), v = t.getAttribute("data-v");
    if (S.confirmDel && a !== "deleteSaved" && a !== "deletePreset") S.confirmDel = null;
    var d = openItem();
    switch (a) {
      case "menu": S.menu = !S.menu; render(); break;
      case "retry": connect(); break;
      // Ler de novo volta para a escolha do trecho (In/Out ou sequência), com o que estiver marcado agora.
      case "refresh": if (!S.busy) { if (S.pid && S.transcript) S.backPid = S.pid; clearProject(); S.scopeChosen = false; refreshSequence(true).then(render); } break;
      case "backDoc": if (S.backPid && !S.busy) { var bp = S.backPid; S.backPid = null; loadProject(bp).then(function () { S.screen = "doc"; render(); }, function (e) { toast("Não reabri as legendas: " + e.message, "err"); }); } break;
      case "scope": S.scope = v; S.scopeChosen = true; render(); break;
      case "legendar": legendar(); break;
      case "cancel": cancel(); break;
      case "home": S.screen = S.transcript ? "doc" : "inicio"; S.error = null; render(); break;
      case "doc": S.screen = "doc"; render(); focusSoon(S.lastSheet === "voices" ? '[data-act="voices"]' : '[data-act="lib"]'); break;
      case "lib": S.screen = "lib"; S.lastSheet = "lib"; S.animCard = null; render(); focusSoon('.specs.all [tabindex="0"]'); break;
      case "voices": S.screen = "voices"; S.lastSheet = "voices"; render(); break;
      case "wordPanel": S.wordPanel = !(S.wordPanel == null ? (S.sel && hasWordStyle(S.sel)) : S.wordPanel); render(); break;
      case "pick":
        var nowPick = Date.now();
        if (t.classList.contains("w") && lastPick && lastPick.id === v && nowPick - lastPick.t < 450) { lastPick = null; startInline(v); break; }
        lastPick = { id: v, t: nowPick };
        pickWord(v); render(); break;
      case "goSave": S.screen = "lib"; render(); focusSoon("#presetName"); break;
      case "saveWord": if (S.sel && S.draft.trim()) ops([{ kind: "replace_text", word_id: S.sel, text: S.draft.trim() }], "Correção salva e travada."); break;
      case "listenWord": var lw = wordById(S.sel); if (lw) listen(lw.start_us, lw.end_us); break;
      case "seekWord": var sw = wordById(S.sel); if (sw) seekTo(sw.start_us); break;
      case "listenItem": if (d) listen(d.start_us, d.end_us); break;
      case "resolve": if (d) resolveItem(d, v); break;
      case "preset": setStyle({}, v); break;
      case "savePreset": saveOwnStyle(); break;
      case "savedPreset": var saved = S.savedStyles[Number(v)]; if (saved) { setStyle(styleOnly(saved.overrides), saved.preset); toast("“" + saved.name + "” na prévia. Cores das vozes e ajustes por palavra preservados."); } break;
      case "deleteSaved": case "deletePreset":
        var cid = t.getAttribute("data-card-id");
        if (S.confirmDel !== cid) { S.confirmDel = cid; render(); break; }
        S.confirmDel = null;
        if (a === "deleteSaved") deleteSaved(Number(v)); else deletePreset(v);
        render(); focusSoon('.specs.all [tabindex="0"]'); break;
      case "restorePresets": restorePresets(); render(); break;
      case "exportSaved": exportSaved(); break;
      case "importSaved": importSaved(); break;
      case "closeImport": S.importReport = null; render(); break;
      case "unpick": S.sel = null; S.draft = ""; S.wordPanel = false; render(); break;
      case "wordReset": if (S.sel) { restoreWord(S.sel); render(); } break;
      case "wordSet": if (S.sel) { var wk = t.getAttribute("data-k"), wp = {}; wp[wk] = v === "true" ? true : v === "false" ? false : null; setWordStyle(S.sel, wp); } break;
      case "rediarize": rediarize(); break;
      case "listenSpeaker": var sample = S.transcript.words.find(function (w) { return w.speaker_id === v; }); if (sample) listen(sample.start_us, sample.start_us + 2200000); break;
      case "styleSet": var k = t.getAttribute("data-k"); var p = {}; p[k] = v === "true" ? true : v === "false" ? false : /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v; setStyle(p); break;
      case "resetPlace":
        var baseSpec = (catalogEntry(S.style.preset) || {}).spec || {};
        PLACE_ONLY.forEach(function (pk) { delete S.style.overrides[pk]; if (S.style.resolved && baseSpec[pk] !== undefined) S.style.resolved[pk] = baseSpec[pk]; });
        setStyle({}); break;
      case "adv": S.adv = !S.adv; render(); break;
      case "safe": S.safe = !S.safe; render(); break;
      case "resetPos": setStyle({ offset_x_pct: 0, offset_y_pct: 0 }); break;
      case "apply": apply(); break;
      case "remove": removeApplied(); break;
      case "space": openSpace(); break;
      case "freeSpace": freeSpace(); break;
      case "spaceBack": S.screen = S.backScreen && S.backScreen !== "space" ? S.backScreen : (S.transcript ? "doc" : "inicio"); render(); break;
      case "wide":
        try { host.openExternal(S.conn.base + "/ui/#token=" + encodeURIComponent(S.conn.token) + "&project=" + S.pid); toast("Aberto no navegador."); }
        catch (e) { toast("Não abri a janela grande: " + e.message + ". Alternativa: alargue o painel (a partir de 900 px vira duas colunas).", "err"); }
        break;
      case "cancelApply": if (S.job && S.job.id) { S.cancelRequested = true; api("DELETE", "/v1/jobs/" + S.job.id).catch(function () {}); render(); } break;
    }
  });

  $app.addEventListener("input", function (ev) {
    var f = ev.target.getAttribute("data-input");
    if (f === "draft") S.draft = ev.target.value;
    if (ev.target.id === "inlineEdit") { S.inlineDraft = ev.target.value; ev.target.size = Math.min(28, Math.max(2, ev.target.value.length + 1)); }
    if (f === "dictionary") S.dictionary = ev.target.value;
    if (f === "presetName") S.presetName = ev.target.value;
    if (f === "fontQuery") { S.fontQuery = ev.target.value; render(); }
    // Valor do controle deslizante aparece enquanto arrasta; o estilo só é salvo no "change".
    if (ev.target.type === "range") { var out = $app.querySelector('output[for="' + ev.target.id + '"]'); if (out) { var nv = ev.target.value.replace(".", ","); out.textContent = /-?\d+(?:[.,]\d+)?/.test(out.textContent) ? out.textContent.replace(/-?\d+(?:[.,]\d+)?/, nv.replace(/^-/, /abaixo|acima|direita|esquerda/.test(out.textContent) ? "" : "-")) : nv; } }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.target.id === "inlineEdit") {
      if (ev.key === "Enter") { ev.preventDefault(); endInline(true); return; }
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); endInline(false); return; }
      return;
    }
    if (ev.key === "Escape") {
      if (S.menu) { S.menu = false; render(); focusSoon('[data-act="menu"]'); return; }
      if (S.screen === "space") { S.screen = S.backScreen && S.backScreen !== "space" ? S.backScreen : (S.transcript ? "doc" : "inicio"); render(); return; }
      if (S.screen === "lib" || S.screen === "voices") { var back = S.screen; S.screen = "doc"; render(); focusSoon('[data-act="' + back + '"]'); return; }
      if (S.sel) { var was = S.sel; S.sel = null; S.wordPanel = false; render(); focusSoon('.w[data-v="' + was + '"]'); return; }
    }
    if (ev.target.id === "corr" && ev.key === "Enter") { ev.preventDefault(); if (S.sel && S.draft.trim()) ops([{ kind: "replace_text", word_id: S.sel, text: S.draft.trim() }], "Correção salva e travada."); return; }
    if (ev.target.id === "presetName" && ev.key === "Enter") { ev.preventDefault(); saveOwnStyle(); return; }
    // Biblioteca: uma parada de Tab; setas percorrem as amostras, Delete apaga o visual.
    var card = ev.target.closest && ev.target.closest(".specs.all .spec");
    if (card) {
      var all = Array.prototype.slice.call($app.querySelectorAll(".specs.all .spec"));
      var ci = all.indexOf(card), target = null;
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") target = all[ci + 1];
      else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") target = all[ci - 1];
      else if (ev.key === "Home") target = all[0];
      else if (ev.key === "End") target = all[all.length - 1];
      else if (ev.key === "Delete" && /^(preset|savedPreset)$/.test(card.getAttribute("data-act"))) {
        ev.preventDefault();
        var dk = card.getAttribute("data-card"), dv = card.getAttribute("data-v");
        if (S.confirmDel !== dk) { S.confirmDel = dk; toast("Aperte Delete de novo para apagar “" + card.getAttribute("aria-label") + "”."); return; }
        S.confirmDel = null;
        if (card.getAttribute("data-act") === "preset") deletePreset(dv); else deleteSaved(Number(dv));
        render(); focusSoon('.specs.all [tabindex="0"]'); return;
      }
      if (target) {
        ev.preventDefault();
        all.forEach(function (c) { c.tabIndex = -1; }); target.tabIndex = 0; target.focus();
        target.scrollIntoView({ block: "nearest" });
      }
    }
  });

  $app.addEventListener("change", function (ev) {
    var el = ev.target, c = el.getAttribute("data-change"), v = el.getAttribute("data-v");
    if (c === "font") setStyle({ font_family: el.value });
    else if (c === "mode") { S.mode = el.value; S.applyResult = null; render(); }
    else if (c === "speakerCount") S.speakerCount = el.value;
    else if (c === "speakerIncluded" && !el.checked && activeSpeakers().filter(function (sp) { return sp.include_in_captions !== false; }).length <= 1) {
      el.checked = true; toast("Pelo menos uma voz precisa ficar nas legendas.", "err");
    }
    else if (c === "speakerIncluded") ops([{ kind: "set_speaker_included", speaker_id: v, included: el.checked }], "Voz atualizada; aplique a mudança na sequência.");
    else if (c === "styleValue" || c === "styleNumber") { var stylePatch = {}; stylePatch[el.getAttribute("data-k")] = c === "styleNumber" ? Number(el.value) : el.value; setStyle(stylePatch); }
    else if (c === "sizePx") { var hpx = seqHeight(), want = Math.max(8, Math.min(Math.round(hpx * 0.3), Number(el.value) || 0)); setStyle({ font_size_pct: Math.round(want / hpx * 10000) / 100 }); }
    else if (c === "wobbleRate") setStyle({ wobble_ms: wobbleMs(Number(el.value)) });
    else if (c === "wobbleAmount") setStyle({ wobble_pct: wobblePct(Number(el.value)) });
    else if (c === "margin") setStyle({ margin_pct: Number(el.value) });
    else if (c === "outline") setStyle({ outline_px: Number(el.value) });
    else if (c === "color") {
      var ck = el.getAttribute("data-k"), p = {}, tgt = voiceTarget(resolved());
      p[ck] = el.value.toUpperCase();
      // Cor escolhida à mão vence a cor por voz (antes o seletor não mudava nada no vídeo).
      if ((ck === "text_color" && tgt === "text") || (ck === "highlight_color" && tgt === "highlight")) {
        p.speaker_color_target = "none";
        toast("Cor por voz agora é Única: a cor escolhida vale para todas as vozes.");
      }
      setStyle(p);
    }
    else if (c === "importFile") {
      var file = el.files && el.files[0];
      if (file) { if (file.size > 262144) { S.importReport = { added: 0, problems: ["arquivo maior que 256 KB"] }; render(); }
        else file.text().then(importText, function (e) { toast("Não li o arquivo: " + e.message, "err"); }); }
    }
    else if (c === "wordNumber" && S.sel) setWordStyle(S.sel, { font_scale: Number(el.value) });
    else if (c === "wordFont" && S.sel) setWordStyle(S.sel, { font_family: el.value || null });
    else if (c === "wordColorOn" && S.sel) setWordStyle(S.sel, { text_color: el.checked ? (resolved().text_color || "#FFFFFF") : null });
    else if (c === "wordColor" && S.sel) setWordStyle(S.sel, { text_color: el.value.toUpperCase() });
    else if (c === "wordBreak" && S.sel) setWordStyle(S.sel, { break_before: el.checked });
    else if (c === "spcolor") ops([{ kind: "set_speaker_color", speaker_id: v, color: el.value.toUpperCase() }], "Cor da voz atualizada.");
    else if (c === "spname") ops([{ kind: "rename_speaker", speaker_id: v, display_name: el.value.trim() || null }], "Nome atualizado.");
    else if (c === "setSpeaker" && S.sel) {
      if (el.value === "__new") ops([{ kind: "add_speaker" }], "Voz criada; escolha-a na lista.");
      else ops([{ kind: "set_speaker", word_ids: [S.sel], speaker_id: el.value }], "Voz trocada e travada.");
    }
  });

  // Amostras: só a que está sob o ponteiro ou em foco toca a entrada, uma vez, sem re-renderizar.
  /* Amostra sob o ponteiro: reproduz o efeito ali mesmo (entrada + palavra a palavra), devagar,
     em volta enquanto o ponteiro estiver em cima. Só ela anima; sair devolve o quadro parado.
     O passo vive fora do DOM: um redesenho no meio continua de onde estava. */
  var cardTimer = null, cardStep = 0;
  var STEP_MS = 560;
  function itemById(id) {
    return catalogItems().concat(savedItems()).find(function (it) { return it.id === id; }) || null;
  }
  function cardEl(id) { return id ? document.querySelector('.spec[data-card="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]') : null; }
  function reducedMotion() { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }
  function paintCard() {
    var el = cardEl(S.animCard), it = itemById(S.animCard);
    if (!el || !it) return;
    el.classList.add("playing");
    if (reducedMotion()) return;
    var n = sampleWords().length, chip = el.querySelector(".sp-chip");
    if (chip) chip.innerHTML = specimen(it.spec, Math.min(cardStep, n - 1), cardStep === 0);
  }
  function playCard(id) {
    if (S.animCard === id) return;
    var old = cardEl(S.animCard), oldIt = itemById(S.animCard);
    if (old) { old.classList.remove("playing"); var oc = old.querySelector(".sp-chip"); if (oc && oldIt) oc.innerHTML = specimen(oldIt.spec); }
    S.animCard = id; cardStep = 0;
    clearInterval(cardTimer); cardTimer = null;
    if (!id) return;
    paintCard();
    if (reducedMotion()) return;
    cardTimer = setInterval(function () {
      var n = sampleWords().length;
      cardStep = cardStep >= n + 1 ? 0 : cardStep + 1;   // fica um instante na última palavra e recomeça
      paintCard();
    }, STEP_MS);
  }
  $app.addEventListener("mouseover", function (ev) { var c = ev.target.closest && ev.target.closest("[data-card]"); if (c) playCard(c.getAttribute("data-card")); });
  $app.addEventListener("focusin", function (ev) {
    var c = ev.target.closest && ev.target.closest("[data-card]");
    if (c) S.focusCard = c.getAttribute("data-card");
    playCard(c ? c.getAttribute("data-card") : null);
  });
  $app.addEventListener("mouseout", function (ev) {
    var g = ev.target.closest && ev.target.closest("[data-gallery]");
    if (g && !(ev.relatedTarget && g.contains(ev.relatedTarget))) playCard(null);
  });

  $app.addEventListener("focusout", function (ev) {
    if (ev.target.id !== "inlineEdit") return;
    setTimeout(function () { var a = document.activeElement; if (S.inlineEdit && !(a && a.id === "inlineEdit")) endInline(true); }, 0);
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(function () { S.frameW = null; render(); }, 120); });

  // Torção na prévia e nas amostras: troca a semente do ruído no ritmo do estilo (só se houver algo com torção na tela).
  var wobbleSeed = 1;
  (function wobbleTick() {
    var st = S.style && S.style.resolved;
    var noise = document.getElementById("dc-wobble-noise");
    if (noise && !reducedMotion() && document.querySelector(".wobble")) noise.setAttribute("seed", String(wobbleSeed = wobbleSeed % 6 + 1));
    setTimeout(wobbleTick, Math.max(60, (st && st.wobble_ms) || 220));
  })();

  setInterval(function () {
    if (!drag && S.screen === "doc" && !S.sel) {
      S.karaokeTick++; refreshFrame();
    }
  }, 800);

  window.addEventListener("focus", function () { if (S.phase === "ready" && !S.busy && S.screen === "inicio") refreshSequence().then(render); });

  // Janela grande no navegador: o projeto vem na URL.
  var hp = new URLSearchParams(location.hash.slice(1));
  connect().then(async function () {
    var pid = hp.get("project");
    if (pid && S.phase === "ready" && !S.pid) { try { await loadProject(pid); S.screen = "doc"; } catch (e) { /* projeto removido */ } render(); }
  });

  window.DC_STATE = S; // para testes automatizados
})();
