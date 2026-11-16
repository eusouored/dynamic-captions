/* Adaptadores do "host": quem fala com o Premiere e encontra o motor.
 *
 * - CEP (painel HTML do Premiere, ExtendScript): caminho principal. As funções de jsx/host.jsx
 *   foram validadas no Premiere 26.5.1 num projeto de teste.
 * - Navegador comum: sem Premiere. Revisão e estilo funcionam; ações de timeline ficam
 *   desabilitadas e rotuladas.
 *
 * Interface comum (todas assíncronas):
 *   kind, canPremiere, connect(), getSequence(), findAudioPresets(), exportAudio(path, preset, useInOut),
 *   seek(us), applyAnimated(clips, guid, previous), removeAnimated(guid, previous),
 *   applyNative(srtPath, startUs, guid),
 *   exportFrame(us, path), openExternal(url)
 */
(function () {
  "use strict";

  function parseResult(raw) {
    if (raw === "EvalScript error.") throw new Error("o Premiere recusou o comando (EvalScript error)");
    var data;
    try { data = JSON.parse(raw); } catch (e) { throw new Error("resposta inválida do Premiere: " + String(raw).slice(0, 200)); }
    if (!data.ok) {
      var err = new Error(data.error || "erro no Premiere");
      err.data = data; // ex.: manifesto parcial quando a timeline já foi alterada
      throw err;
    }
    return data;
  }

  var WAIT_USE = "Clique no painel para iniciar o Dynamic Captions.";

  function cepHost() {
    var cep = window.__adobe_cep__;
    var node = typeof window.cep_node !== "undefined" ? window.cep_node : null;
    var req = node && node.require ? node.require : (typeof require === "function" ? require : null);
    var fs = req ? req("fs") : null;
    var path = req ? req("path") : null;
    var os = req ? req("os") : null;
    var childProcess = req ? req("child_process") : null;

    /* Premiere abrindo: o painel (AutoVisible) carrega junto com ele. Até o editor usar o painel
       (clique ou tecla), nada pesado acontece: nem o motor Python (GPU), nem ExtendScript, que
       roda na thread principal do Premiere enquanto ele ainda abre o projeto. */
    var used = false, usedWaiters = [];
    function markUsed() {
      if (used) return;
      used = true;
      usedWaiters.splice(0).forEach(function (f) { f(); });
    }
    if (typeof window.addEventListener === "function") {
      ["pointerdown", "mousedown", "keydown"].forEach(function (ev) { window.addEventListener(ev, markUsed, true); });
    }
    function whenUsed() { return used ? Promise.resolve() : new Promise(function (r) { usedWaiters.push(r); }); }

    var EVAL_TIMEOUT_MS = 60000; // exportar áudio longo leva tempo; isto só evita esperar para sempre
    function evalRaw(script) {
      return new Promise(function (resolve, reject) {
        var t = setTimeout(function () { reject(new Error("o Premiere não respondeu (ocupado ou ainda abrindo); tente de novo")); }, EVAL_TIMEOUT_MS);
        try {
          cep.evalScript(script, function (raw) { clearTimeout(t); resolve(raw); });
        } catch (e) { clearTimeout(t); reject(e); }
      });
    }

    /* Se o host.jsx falhou ao carregar com o Premiere ainda abrindo ("Error evaluating script"
       no log do CEP), as funções dc_* não existem: carrega de novo em vez de falhar até reabrir. */
    var scriptReady = null;
    function extensionDir() {
      var raw = typeof cep.getSystemPath === "function" ? cep.getSystemPath("extension") : "";
      try { raw = decodeURI(String(raw || "")); } catch (e) { raw = String(raw || ""); }
      return raw.replace(/^file:\/\/\/(?=[A-Za-z]:)/, "").replace(/^file:\/\//, "");
    }
    function ensureScript() {
      if (scriptReady) return scriptReady;
      var attempt = scriptReady = (async function () {
        if ((await evalRaw("typeof dc_getSequence")) === "function") return;
        var dir = extensionDir();
        if (dir) await evalRaw("$.evalFile(new File(" + JSON.stringify(dir + "/jsx/host.jsx") + "))");
        if ((await evalRaw("typeof dc_getSequence")) !== "function") {
          throw new Error("o Premiere ainda não carregou os comandos do Dynamic Captions; tente de novo em alguns segundos");
        }
      })();
      attempt.catch(function () { if (scriptReady === attempt) scriptReady = null; });
      return attempt;
    }

    async function call(fn, args) {
      await whenUsed();
      await ensureScript();
      var script = fn + "(" + (args || []).map(function (a) { return JSON.stringify(a); }).join(",") + ")";
      return parseResult(await evalRaw(script));
    }

    function appDir() {
      var base = (process && process.env && process.env.LOCALAPPDATA) || (os ? path.join(os.homedir(), "AppData", "Local") : "");
      return path.join(base, "DynamicCaptions");
    }

    function readJson(file) {
      try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) { return null; }
    }

    function fetchT(url, opts, ms) {
      if (typeof AbortController !== "function") return fetch(url, opts);
      var c = new AbortController(), t = setTimeout(function () { c.abort(); }, ms || 3000);
      return fetch(url, Object.assign({}, opts || {}, { signal: c.signal })).finally(function () { clearTimeout(t); });
    }

    async function health(info) {
      try {
        if (!info.token) return false;
        // A porta pode responder durante uma reinicialização com o token anterior.
        // Só conecte depois que o arquivo e o serviço concordarem.
        var r = await fetchT("http://127.0.0.1:" + info.port + "/v1/projects", {
          cache: "no-store", headers: { "X-DC-Token": info.token }
        });
        return r.ok;
      } catch (e) { return false; }
    }

    async function engineVersion(port) {
      try {
        var r = await fetchT("http://127.0.0.1:" + port + "/v1/health", { cache: "no-store" });
        var d = await r.json();
        return d && d.status === "ok" ? String(d.version || "") : null;
      } catch (e) { return null; }
    }

    /* PID que escuta numa porta local (netstat existe em todo Windows; a coluna de estado
       muda com o idioma, então só endereço local, remoto e PID são lidos). */
    function pidOnPort(port) {
      try {
        var out = childProcess.execSync("netstat -ano -p tcp", { windowsHide: true, encoding: "utf8", timeout: 5000 });
        var re = new RegExp("TCP\\s+127\\.0\\.0\\.1:" + port + "\\s+0\\.0\\.0\\.0:0\\s+\\S+\\s+(\\d+)");
        var m = out.match(re);
        return m ? Number(m[1]) : null;
      } catch (e) { return null; }
    }

    function isPython(pid) {
      try {
        var out = childProcess.execSync('tasklist /FI "PID eq ' + Number(pid) + '" /FO CSV /NH', { windowsHide: true, encoding: "utf8", timeout: 5000 });
        return /"python[^"]*\.exe"/i.test(out);
      } catch (e) { return false; }
    }

    function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

    /* Encerra um motor do Dynamic Captions de outra versão: pede para sair; se for antigo e não
       souber, encerra o processo que escuta na porta (só depois de a porta responder como
       Dynamic Captions). Assim uma atualização não deixa dois motores ocupando a GPU. */
    /* Painel fechando (o Premiere fechou ou o painel foi fechado): pede ao motor para sair.
       O vigia do motor (api/vigia.py) garante a saída mesmo se este pedido não chegar. */
    var lastConn = null;
    if (typeof window.addEventListener === "function") {
      window.addEventListener("unload", function () {
        if (!lastConn) return;
        try { fetch(lastConn.base + "/v1/engine/shutdown", { method: "POST", headers: { "X-DC-Token": lastConn.token }, keepalive: true }); } catch (e) { /* saindo */ }
      });
    }

    async function stopEngine(port, token, pid) {
      if (token) {
        try { await fetch("http://127.0.0.1:" + port + "/v1/engine/shutdown", { method: "POST", headers: { "X-DC-Token": token } }); } catch (e) { /* antigo */ }
        for (var i = 0; i < 10 && (await engineVersion(port)) !== null; i++) await sleep(300);
      }
      if ((await engineVersion(port)) === null) return true;
      // O PID guardado no engine.json só serve se ainda for um Python (o Windows reaproveita PIDs).
      var target = pidOnPort(port) || (pid && isPython(pid) ? pid : null);
      if (!target) return false;
      try { process.kill(target); } catch (e) { return false; }
      for (var j = 0; j < 20 && (await engineVersion(port)) !== null; j++) await sleep(250);
      return (await engineVersion(port)) === null;
    }

    var api = {
      kind: "cep",
      canPremiere: true,
      waitForUse: function () { return used ? null : whenUsed(); },
      /* Encontra o motor pelo engine.json; se não estiver rodando, inicia com o launch.json
       * gravado pelo instalador. Nada disso aparece para o editor, só "Conectando". */
      connect: async function (onStatus, want) {
        if (!fs) throw new Error("o painel foi aberto sem acesso a arquivos locais (Node desabilitado)");
        var dir = appDir();
        var info = readJson(path.join(dir, "engine.json"));
        if (info && (await health(info))) {
          var have = await engineVersion(info.port);
          if (!want || have === null || have === want) return { base: "http://127.0.0.1:" + info.port, token: info.token };
          if (!used && onStatus) onStatus(WAIT_USE);
          await whenUsed();
          if (onStatus) onStatus("Atualizando o motor…");
          await stopEngine(info.port, info.token, info.pid);
        }
        // Motores esquecidos de versões anteriores (sem engine.json) também saem.
        if (want) {
          for (var port = 47310; port <= 47314; port++) {
            var v = await engineVersion(port);
            if (v !== null && v !== want) { await whenUsed(); await stopEngine(port, null, null); }
          }
        }
        var launch = readJson(path.join(dir, "launch.json"));
        if (!launch) throw new Error("NEEDS_SETUP");
        // O motor carrega os modelos na GPU: só sobe quando o editor usa o painel.
        if (!used && onStatus) onStatus(WAIT_USE);
        await whenUsed();
        if (onStatus) onStatus("Iniciando o Dynamic Captions…");
        var child = childProcess.spawn(launch.python, ["-m", "dynamic_captions.api"], {
          cwd: launch.cwd || dir, detached: true, windowsHide: true, stdio: "ignore",
          // O motor sobe a partir daqui até achar o Premiere e fecha quando ele fechar (api/vigia.py).
          env: Object.assign({}, process.env, launch.env || {}, { DYNAMIC_CAPTIONS_PANEL_PID: String(process.pid) })
        });
        child.unref();
        for (var i = 0; i < 60; i++) {
          await new Promise(function (r) { setTimeout(r, 500); });
          info = readJson(path.join(dir, "engine.json"));
          if (info && (await health(info))) {
            var conn = { base: "http://127.0.0.1:" + info.port, token: info.token };
            // O launch.json aponta para um motor de outra versão: conecta, mas avisa, em vez de
            // encerrar e reiniciar o mesmo motor a cada abertura do painel.
            var got = want ? await engineVersion(info.port) : null;
            if (got && got !== want) conn.versionMismatch = got;
            return conn;
          }
        }
        throw new Error("ENGINE_TIMEOUT");
      },
      getSequence: function () { return call("dc_getSequence").then(function (d) { return d.sequence; }); },
      findAudioPresets: function () { return call("dc_findAudioPresets").then(function (d) { return d.presets; }); },
      exportAudio: function (out, preset, useInOut) { return call("dc_exportAudio", [out, preset, !!useInOut]); },
      exportAudioContext: function (out, preset, start, end, guid) {
        return call("dc_exportAudioContext", [out, preset, start, end, guid]);
      },
      seek: function (us) { return call("dc_seek", [us]); },
      /* Contrato completo: path, key, seq_start_us e seq_end_us de cada bloco, a sequência
       * obrigatória e o manifesto do que este projeto colocou antes (propriedade). */
      applyAnimated: function (clips, guid, previous) {
        var full = clips.map(function (c) { return { path: c.path, key: c.key, label: c.label, seq_start_us: c.seq_start_us, seq_end_us: c.seq_end_us }; });
        return call("dc_applyAnimated", [full, guid, previous || []]);
      },
      releaseClips: function (paths) { return call("dc_releaseClips", [paths]); },
      removeAnimated: function (guid, previous) { return call("dc_removeAnimated", [guid, previous || []]); },
      applyNative: function (srt, startUs, guid) { return call("dc_applyNative", [srt, startUs, guid]); },
      exportFrame: function (us, out, guid, previous) { return call("dc_exportFrame", [us, out, guid, previous || []]); },
      /* API suportada do CEP (a mesma que o CSInterface.js usa): cep.util.openURLInDefaultBrowser. */
      openExternal: function (url) {
        var util = window.cep && window.cep.util;
        if (!util || typeof util.openURLInDefaultBrowser !== "function") throw new Error("este Premiere não oferece abrir o navegador pelo painel");
        var r = util.openURLInDefaultBrowser(url);
        if (r && r.err) throw new Error("o navegador não abriu (código " + r.err + ")");
        return true;
      }
    };
    var connectInner = api.connect;
    api.connect = async function (onStatus, want) {
      var c = await connectInner(onStatus, want);
      lastConn = c;
      return c;
    };
    return api;
  }

  function browserHost() {
    function unavailable() { return Promise.reject(new Error("disponível só dentro do Premiere")); }
    return {
      kind: "browser",
      canPremiere: false,
      connect: async function () {
        var p = new URLSearchParams(location.hash.slice(1));
        var token = p.get("token") || sessionStorage.getItem("dc-token") || "";
        if (token) { try { sessionStorage.setItem("dc-token", token); } catch (e) { /* sem armazenamento */ } }
        if (!token) throw new Error("NEEDS_TOKEN");
        return { base: location.origin, token: token };
      },
      getSequence: function () { return Promise.resolve(null); },
      findAudioPresets: function () { return Promise.resolve([]); },
      exportAudio: unavailable, seek: unavailable, applyAnimated: unavailable,
      removeAnimated: unavailable, applyNative: unavailable, exportFrame: unavailable,
      openExternal: function (url) { window.open(url, "_blank"); }
    };
  }

  window.DCHost = window.DCHostOverride || (window.__adobe_cep__ ? cepHost() : browserHost());
})();
