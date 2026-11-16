/* Dynamic Captions — ponte com o Premiere (ExtendScript, executada pelo painel CEP).
 *
 * Cada função devolve uma string JSON {ok: true, ...} ou {ok: false, error: "..."}.
 * Funções validadas no Premiere 26.5.1 (7/11/2026) num projeto de teste:
 *   exportAsMediaDirect (sequência inteira e In/Out), importFiles, QE addTracks,
 *   overwriteClip em faixa nova, remove de clipes e do áudio vinculado, createCaptionTrack.
 */

var DC_TICKS = 254016000000;
var DC_BIN = "Dynamic Captions";

function dc__q(s) {
    s = String(s);
    var out = '"';
    for (var i = 0; i < s.length; i++) {
        var c = s.charAt(i), code = s.charCodeAt(i);
        if (c === '"') out += '\\"';
        else if (c === "\\") out += "\\\\";
        else if (c === "\n") out += "\\n";
        else if (c === "\r") out += "\\r";
        else if (c === "\t") out += "\\t";
        else if (code < 32) out += "\\u" + ("0000" + code.toString(16)).slice(-4);
        else out += c;
    }
    return out + '"';
}

function dc__json(v) {
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    if (t === "string") return dc__q(v);
    if (v instanceof Array) {
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(dc__json(v[i]));
        return "[" + a.join(",") + "]";
    }
    var parts = [];
    for (var k in v) if (v.hasOwnProperty(k)) parts.push(dc__q(k) + ":" + dc__json(v[k]));
    return "{" + parts.join(",") + "}";
}

function dc__ok(o) { o = o || {}; o.ok = true; return dc__json(o); }
function dc__err(msg) { return dc__json({ ok: false, error: String(msg) }); }

function dc__seq() {
    if (!app.project) return null;
    return app.project.activeSequence || null;
}

function dc__fps(timebase) {
    var fps = DC_TICKS / Number(timebase);
    var known = [[24000, 1001], [30000, 1001], [60000, 1001], [48000, 1001]];
    for (var i = 0; i < known.length; i++) {
        if (Math.abs(fps - known[i][0] / known[i][1]) < 0.01) return known[i];
    }
    if (Math.abs(fps - Math.round(fps)) < 0.001) return [Math.round(fps), 1];
    return [Math.round(fps * 1000), 1000];
}

/* Sequência ativa: nome, id, quadro, taxa, duração e In/Out (em µs). */
function dc_getSequence() {
    try {
        var s = dc__seq();
        if (!s) return dc__ok({ sequence: null });
        var fr = dc__fps(s.timebase);
        var inS = Number(s.getInPoint()), outS = Number(s.getOutPoint());
        var endS = Number(s.end) / DC_TICKS;
        var hasInOut = inS >= 0 && outS > inS && !(inS === 0 && Math.abs(outS - endS) < 0.001);
        var contentS = dc__contentEnd(s, endS);
        var vdf = -1;
        try { vdf = Number(s.getSettings().videoDisplayFormat); } catch (e2) { vdf = -1; }
        return dc__ok({
            sequence: {
                guid: s.sequenceID,
                name: s.name,
                width: Number(s.frameSizeHorizontal),
                height: Number(s.frameSizeVertical),
                fps_num: fr[0],
                fps_den: fr[1],
                end_us: Math.round(endS * 1e6),
                // Fim do conteúdo sem contar as legendas do Dynamic Captions (o s.end inclui o clipe de legenda).
                content_end_us: Math.round(contentS * 1e6),
                in_us: hasInOut ? Math.round(inS * 1e6) : null,
                out_us: hasInOut ? Math.round(outS * 1e6) : null,
                // TimeDisplay do Premiere: 102 = 29,97 drop-frame, 106 = 59,94 drop-frame.
                drop_frame: vdf === 102 || vdf === 106,
                project: app.project.name
            }
        });
    } catch (e) { return dc__err(e); }
}

/* Fim do último clipe que NÃO é legenda renderizada pelo Dynamic Captions, em segundos.
 * Os clipes de cada faixa vêm em ordem de tempo: basta andar de trás para frente até o primeiro
 * que não é nosso. Medida só (nunca usada para decidir o que remover). */
function dc__isRender(path) {
    return path.indexOf("/dynamiccaptions/") >= 0 && path.indexOf("/renders/") >= 0 || /\/dc_[0-9a-f]{12}\.mov$/.test(path);
}
function dc__contentEnd(s, fallback) {
    var best = 0, found = false, groups = [s.videoTracks, s.audioTracks];
    for (var g = 0; g < groups.length; g++) {
        for (var t = 0; t < groups[g].numTracks; t++) {
            var clips = groups[g][t].clips;
            for (var c = clips.numItems - 1; c >= 0; c--) {
                var clip = clips[c];
                if (dc__isRender(dc__clipPath(clip))) continue;
                var e = clip.end.seconds;
                if (e > best) best = e;
                found = true;
                break;
            }
        }
    }
    return found && best > 0 ? Math.min(best, fallback) : fallback;
}

/* Exporta o áudio mixado da sequência editada (inteira ou In/Out) para um WAV.
 * workAreaType: 0 = sequência inteira, 1 = entre In e Out. Síncrono (alguns segundos). */
function dc_exportAudio(outPath, presetPath, useInOut) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        if (!new File(presetPath).exists) return dc__err("preset de áudio não encontrado: " + presetPath);
        var f = new File(outPath);
        if (f.exists) f.remove();
        var startS = useInOut ? Number(s.getInPoint()) : 0;
        var endS = useInOut ? Number(s.getOutPoint()) : Number(s.end) / DC_TICKS;
        var t0 = new Date().getTime();
        var r = s.exportAsMediaDirect(outPath, presetPath, useInOut ? 1 : 0);
        f = new File(outPath);
        if (!f.exists || f.length < 100) return dc__err("o Premiere não gravou o áudio: " + r);
        return dc__ok({ path: outPath, start_us: Math.round(startS * 1e6), end_us: Math.round(endS * 1e6),
                        bytes: f.length, ms: new Date().getTime() - t0, sequence_guid: s.sequenceID });
    } catch (e) { return dc__err(e); }
}

/* Contexto adicional só para distinguir vozes em trechos curtos. In/Out restaurados. */
function dc__restorePoint(s, seconds, isOut) {
    function set(value) { if (isOut) s.setOutPoint(value); else s.setInPoint(value); }
    set(seconds);
    var frame = Number(s.timebase) / DC_TICKS;
    if (!(frame > 0) || seconds < 0) return;
    var restored = isOut ? s.getOutPointAsTime().seconds : s.getInPointAsTime().seconds;
    // O setter nativo quantiza em amostras de áudio. Na fronteira de um quadro,
    // isso pode cair alguns microssegundos no quadro anterior (observado a 59,94).
    // Reponha o ponto dentro do mesmo quadro, sem acumular um quadro de deriva.
    var expected = Math.floor(seconds / frame + 1e-7);
    if (Math.floor(restored / frame + 1e-7) < expected) {
        set(seconds + Math.min(frame / 4, 1 / 48000));
    }
}

function dc_exportAudioContext(outPath, presetPath, startUs, endUs, expectedGuid) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        var error = dc__requireGuid(s, expectedGuid);
        if (error) return dc__err(error);
        var oldIn = s.getInPointAsTime().seconds, oldOut = s.getOutPointAsTime().seconds;
        try {
            s.setInPoint(Number(startUs) / 1e6);
            s.setOutPoint(Number(endUs) / 1e6);
            return dc_exportAudio(outPath, presetPath, true);
        } finally {
            dc__restorePoint(s, oldIn, false);
            dc__restorePoint(s, oldOut, true);
        }
    } catch (e) { return dc__err(e); }
}

/* Move o indicador de reprodução (mostrar a palavra na sequência). */
function dc_seek(us) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        s.setPlayerPosition(String(Math.round(Number(us) / 1e6 * DC_TICKS)));
        return dc__ok({});
    } catch (e) { return dc__err(e); }
}

function dc__bin() {
    var root = app.project.rootItem;
    for (var i = 0; i < root.children.numItems; i++) {
        var c = root.children[i];
        if (c.name === DC_BIN && c.type === ProjectItemType.BIN) return c;
    }
    return root.createBin(DC_BIN);
}

function dc__baseName(path) {
    var p = String(path).replace(/\\/g, "/");
    return p.substring(p.lastIndexOf("/") + 1);
}

var DC_TOL = 0.05; // s: tolerância de posição (o Premiere encaixa no quadro)

function dc__norm(p) { return String(p || "").replace(/\\/g, "/").toLowerCase(); }

function dc__clipPath(clip) {
    try {
        var pi = clip.projectItem;
        return pi && pi.getMediaPath ? dc__norm(pi.getMediaPath()) : "";
    } catch (e) { return ""; }
}

/* Propriedade: um clipe só é do Dynamic Captions se o CAMINHO COMPLETO da mídia e a POSIÇÃO
 * batem com o manifesto do projeto (os clipes que o painel colocou nesta sequência).
 * Nome, prefixo ou extensão nunca bastam. */
function dc__owned(previous) {
    var idx = [];
    for (var i = 0; previous && i < previous.length; i++) {
        if (previous[i] && previous[i].path) idx.push({ path: dc__norm(previous[i].path), start: Number(previous[i].seq_start_us) / 1e6 });
    }
    return idx;
}

function dc__isOwned(clip, owned) {
    var p = dc__clipPath(clip);
    if (!p) return false;
    for (var i = 0; i < owned.length; i++) {
        if (owned[i].path === p && Math.abs(owned[i].start - clip.start.seconds) <= DC_TOL) return true;
    }
    return false;
}

// Tolerância de encaixe não vale para conflitos: até um quadro sobreposto destruiria vídeo.
// Um microssegundo elimina só ruído numérico na comparação de limites encostados.
function dc__overlaps(a0, a1, b0, b1) { return a0 < b1 - 0.000001 && b0 < a1 - 0.000001; }

function dc__findItemByPath(bin, path) {
    var want = dc__norm(path);
    for (var i = 0; i < bin.children.numItems; i++) {
        var c = bin.children[i];
        try { if (c.getMediaPath && dc__norm(c.getMediaPath()) === want) return c; } catch (e) { /* bin ou item sem mídia */ }
    }
    return null;
}

function dc__requireGuid(s, expectedGuid) {
    if (!expectedGuid) return "a sequência de destino não foi informada";
    if (s.sequenceID !== expectedGuid) return "a sequência ativa não é a que foi legendada (" + s.name + ")";
    return null;
}

function dc__removeClip(clip) {
    var own = dc__clipPath(clip);
    var links = clip.getLinkedItems ? clip.getLinkedItems() : null;
    if (links) {
        // Só o áudio vinculado da MESMA mídia (os blocos renderizados não têm áudio).
        for (var i = 0; i < links.numItems; i++) {
            if (links[i].mediaType === "Audio" && dc__clipPath(links[i]) === own) links[i].remove(false, false);
        }
    }
    clip.remove(false, false);
}

function dc__label(clip, trackIndex) {
    return "“" + clip.name + "” na V" + (trackIndex + 1) + " em " + clip.start.seconds.toFixed(2) + "–" + clip.end.seconds.toFixed(2) + " s";
}

/* Depois que a timeline mudou, um erro precisa devolver o que ficou nela: os clipes do
   manifesto antigo que sobraram e os novos já colocados (de qualquer faixa). O painel salva
   isso como manifesto, para "aplicar de novo" e "remover" continuarem reconhecendo tudo. */
function dc__partial(s, previous, want) {
    var refs = [], out = [], t, c, i;
    for (i = 0; previous && i < previous.length; i++) {
        if (previous[i] && previous[i].path) refs.push({ path: previous[i].path, norm: dc__norm(previous[i].path), key: previous[i].key, start: Number(previous[i].seq_start_us) / 1e6 });
    }
    for (i = 0; i < want.length; i++) refs.push({ path: want[i].path, norm: want[i].norm, key: want[i].key, start: want[i].start });
    var track = -1;
    for (t = 0; t < s.videoTracks.numTracks; t++) {
        var trk = s.videoTracks[t];
        for (c = 0; c < trk.clips.numItems; c++) {
            var clip = trk.clips[c], p = dc__clipPath(clip);
            if (!p) continue;
            for (i = 0; i < refs.length; i++) {
                if (refs[i].norm === p && Math.abs(refs[i].start - clip.start.seconds) <= DC_TOL) {
                    out.push({ path: refs[i].path, key: refs[i].key, seq_start_us: Math.round(clip.start.seconds * 1e6), seq_end_us: Math.round(clip.end.seconds * 1e6) });
                    if (track < 0) track = t;
                    break;
                }
            }
        }
    }
    return { clips: out, track_index: track, sequence_guid: s.sequenceID };
}

function dc__errPartial(msg, s, previous, want) {
    var partial = null;
    try { partial = dc__partial(s, previous, want); } catch (e) { partial = null; }
    return dc__json({ ok: false, error: String(msg), partial: partial });
}

/* Aplica ou atualiza a legenda animada.
 *   clips: [{path, key, seq_start_us, seq_end_us}] — o que deve ficar na sequência agora.
 *   expectedGuid: sequência legendada (obrigatório).
 *   previous: manifesto do que este projeto colocou antes ([{path, seq_start_us, ...}]).
 * Ordem: valida tudo → importa → remove só clipes do manifesto que mudaram → coloca → confere.
 * Qualquer conflito com um clipe que não é do manifesto recusa a operação ANTES de mudar
 * algo na timeline. Sem faixa do manifesto, usa uma faixa nova e vazia no topo. */
function dc_applyAnimated(clips, expectedGuid, previous) {
    var s = null, want = [], mutated = false;
    try {
        s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        var g = dc__requireGuid(s, expectedGuid);
        if (g) return dc__err(g);
        var owned = dc__owned(previous);
        for (var i = 0; i < clips.length; i++) {
            var c0 = clips[i];
            if (!c0.path || c0.seq_start_us == null || c0.seq_end_us == null) return dc__err("bloco sem caminho ou sem início/fim");
            want.push({ path: c0.path, norm: dc__norm(c0.path), key: c0.key, label: c0.label ? String(c0.label) : "", start: Number(c0.seq_start_us) / 1e6, end: Number(c0.seq_end_us) / 1e6, matched: false });
        }

        // 1) Classificar a timeline inteira: clipes do manifesto (de qualquer faixa) e a faixa alvo.
        var targetIndex = -1, ownedClips = [];
        for (var t = 0; t < s.videoTracks.numTracks; t++) {
            var trk = s.videoTracks[t];
            for (var c = 0; c < trk.clips.numItems; c++) {
                if (dc__isOwned(trk.clips[c], owned)) {
                    ownedClips.push({ clip: trk.clips[c], track: t });
                    if (targetIndex < 0) targetIndex = t;
                }
            }
        }
        var keep = [], drop = [];
        for (var o = 0; o < ownedClips.length; o++) {
            var oc = ownedClips[o], hit = null;
            if (oc.track === targetIndex) {
                for (var w = 0; w < want.length; w++) {
                    if (!want[w].matched && want[w].norm === dc__clipPath(oc.clip) && Math.abs(want[w].start - oc.clip.start.seconds) <= DC_TOL) { hit = want[w]; break; }
                }
            }
            if (hit) { hit.matched = true; keep.push(oc); } else drop.push(oc);
        }

        // 2) Pré-checagem de conflitos na faixa alvo (clipes que não são do manifesto).
        if (targetIndex >= 0) {
            var tr0 = s.videoTracks[targetIndex], conflicts = [];
            for (var f = 0; f < tr0.clips.numItems; f++) {
                var fc = tr0.clips[f];
                if (dc__isOwned(fc, owned)) continue;
                for (var w2 = 0; w2 < want.length; w2++) {
                    if (!want[w2].matched && dc__overlaps(want[w2].start, want[w2].end, fc.start.seconds, fc.end.seconds)) { conflicts.push(dc__label(fc, targetIndex)); break; }
                }
            }
            if (conflicts.length) return dc__err("conflito na faixa das legendas: " + conflicts.join("; ") + ". Nada foi alterado. Libere o trecho ou remova as legendas e aplique de novo numa faixa nova.");
        }

        // 3) Importar o que falta (identificado pelo caminho completo, não pelo nome).
        var bin = dc__bin(), toImport = [];
        for (var j = 0; j < want.length; j++) if (!want[j].matched && !dc__findItemByPath(bin, want[j].path)) toImport.push(want[j].path);
        if (toImport.length) app.project.importFiles(toImport, true, bin, false);
        for (var j2 = 0; j2 < want.length; j2++) {
            var item = dc__findItemByPath(bin, want[j2].path);
            if (!want[j2].matched && !item) return dc__err("o Premiere não importou " + dc__baseName(want[j2].path) + ". Nada foi alterado na timeline.");
            // Nome legível no painel Projeto (o arquivo continua DC_<hash>.mov); falhar aqui não impede aplicar.
            if (item && want[j2].label && item.name !== want[j2].label) { try { item.name = want[j2].label; } catch (eName) { /* só o nome */ } }
        }

        // 4) Mudar a timeline: remover só o que é do manifesto e mudou; criar faixa se preciso; colocar.
        var removed = 0;
        mutated = true;
        for (var d = 0; d < drop.length; d++) { dc__removeClip(drop[d].clip); removed++; }
        var created = false;
        var top = s.videoTracks.numTracks - 1;
        if (targetIndex < 0 && top >= 0 && s.videoTracks[top].clips.numItems === 0) {
            targetIndex = top; // faixa do topo vazia (ex.: legendas removidas antes): reaproveita, sem conflito possível
        }
        if (targetIndex < 0) {
            var before = s.videoTracks.numTracks;
            app.enableQE();
            qe.project.getActiveSequence().addTracks(1, before, 0);
            if (s.videoTracks.numTracks !== before + 1) return dc__errPartial("não foi possível criar a faixa de vídeo das legendas", s, previous, want);
            targetIndex = before; created = true;
        }
        var tr = s.videoTracks[targetIndex], placed = 0;
        for (var p = 0; p < want.length; p++) {
            if (want[p].matched) continue;
            tr.overwriteClip(dc__findItemByPath(bin, want[p].path), want[p].start);
            placed++;
        }

        // 5) Conferir e devolver o manifesto real (posições lidas da timeline).
        var manifest = [], missing = [];
        for (var q = 0; q < want.length; q++) {
            var found = null;
            for (var k = 0; k < tr.clips.numItems; k++) {
                var tc = tr.clips[k];
                if (dc__clipPath(tc) === want[q].norm && Math.abs(tc.start.seconds - want[q].start) <= DC_TOL) { found = tc; break; }
            }
            if (!found) missing.push(dc__baseName(want[q].path));
            else manifest.push({ path: want[q].path, key: want[q].key, seq_start_us: Math.round(found.start.seconds * 1e6), seq_end_us: Math.round(found.end.seconds * 1e6) });
        }
        if (missing.length) return dc__errPartial("conferência falhou: não encontrei " + missing.join(", ") + " na V" + (targetIndex + 1), s, previous, want);
        return dc__ok({ track_index: targetIndex, track_created: created, kept: keep.length, removed: removed, placed: placed, clips: manifest, sequence_guid: s.sequenceID });
    } catch (e) { return mutated && s ? dc__errPartial(e, s, previous, want) : dc__err(e); }
}

/* Clipes que saíram da timeline: devolve quais nenhuma sequência do projeto aberto usa
   ("released") e tira esses itens da pasta Dynamic Captions do painel Projeto, para o arquivo
   poder ir para os descartados sem deixar mídia offline. Os usados ficam ("kept"). */
function dc__findInTree(folder, norm, skip) {
    for (var i = 0; i < folder.children.numItems; i++) {
        var c = folder.children[i];
        if (c === skip) continue;
        if (c.type === ProjectItemType.BIN) { if (dc__findInTree(c, norm, skip)) return true; continue; }
        try { if (c.getMediaPath && dc__norm(c.getMediaPath()) === norm) return true; } catch (e) { /* item sem mídia */ }
    }
    return false;
}

function dc_releaseClips(paths) {
    try {
        if (!app.project) return dc__err("nenhum projeto aberto");
        var used = {}, seqs = app.project.sequences;
        for (var q = 0; seqs && q < seqs.numSequences; q++) {
            var sq = seqs[q];
            for (var t = 0; t < sq.videoTracks.numTracks; t++) {
                var trk = sq.videoTracks[t];
                for (var c = 0; c < trk.clips.numItems; c++) {
                    var p = dc__clipPath(trk.clips[c]);
                    if (p) used[p] = true;
                }
            }
        }
        var bin = dc__bin(), released = [], kept = [], items = [], trash = null;
        for (var i = 0; paths && i < paths.length; i++) {
            if (used[dc__norm(paths[i])]) { kept.push(paths[i]); continue; }
            var item = dc__findItemByPath(bin, paths[i]);
            // Item que o editor levou para outra pasta do projeto: foi organizado de propósito, fica.
            if (!item && dc__findInTree(app.project.rootItem, dc__norm(paths[i]), bin)) { kept.push(paths[i]); continue; }
            if (item) {
                if (!trash) trash = bin.createBin("DC_descartar");
                item.moveBin(trash);
                items.push(item);
            }
            released.push(paths[i]);
        }
        if (trash) {
            try { trash.deleteBin(); }
            catch (eDel) {
                // Sem conseguir apagar os itens, os arquivos não podem sair: desfaz e não libera nada.
                for (var k = 0; k < items.length; k++) { try { items[k].moveBin(bin); } catch (eBack) { /* fica na pasta */ } }
                try { trash.deleteBin(); } catch (eEmpty) { /* pasta vazia */ }
                return dc__ok({ released: [], kept: paths || [] });
            }
        }
        return dc__ok({ released: released, kept: kept });
    } catch (e) { return dc__err(e); }
}

/* Remove da sequência só os clipes do manifesto deste projeto (o "desfazer" do painel). */
function dc_removeAnimated(expectedGuid, previous) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        var g = dc__requireGuid(s, expectedGuid);
        if (g) return dc__err(g);
        var owned = dc__owned(previous), removed = 0;
        for (var t = 0; t < s.videoTracks.numTracks; t++) {
            var tr = s.videoTracks[t];
            for (var c = tr.clips.numItems - 1; c >= 0; c--) {
                if (dc__isOwned(tr.clips[c], owned)) { dc__removeClip(tr.clips[c]); removed++; }
            }
        }
        return dc__ok({ removed: removed, not_found: Math.max(0, owned.length - removed) });
    } catch (e) { return dc__err(e); }
}

/* Legenda nativa: importa o SRT e cria uma faixa de legendas começando em startUs.
 * Limite real: o Premiere não oferece por script como ler ou apagar faixas de legenda;
 * atualizar cria outra faixa (o painel avisa antes). */
function dc_applyNative(srtPath, startUs, expectedGuid) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        var g = dc__requireGuid(s, expectedGuid);
        if (g) return dc__err(g);
        var bin = dc__bin();
        if (!dc__findItemByPath(bin, srtPath)) app.project.importFiles([srtPath], true, bin, false);
        var item = dc__findItemByPath(bin, srtPath);
        if (!item) return dc__err("o Premiere não importou o SRT");
        var r = s.createCaptionTrack(item, Number(startUs) / 1e6);
        if (!r) return dc__err("o Premiere recusou criar a faixa de legendas");
        return dc__ok({ item: item.name });
    } catch (e) { return dc__err(e); }
}

/* Quadro da sequência em PNG (fundo real da prévia de estilo). Usa o QE DOM.
 *
 * Assinatura do QE: exportFramePNG(timecode, caminhoSemExtensao), em que o tempo é um
 * TIMECODE no formato de exibição da sequência (o mesmo de qeSeq.CTI.timecode, ex.
 * "00;00;30;40"), não ticks. Ticks numa string eram lidos como outro tempo (um teste recebeu o
 * quadro inicial ao pedir 30,668 s). Para não formatar timecode à mão (drop-frame, taxas fora
 * do padrão), o indicador vai até o instante, o QE devolve o timecode certo e o indicador
 * volta para onde o editor o deixou. */
function dc_exportFrame(us, outPath, expectedGuid, previousClips) {
    try {
        var s = dc__seq();
        if (!s) return dc__err("nenhuma sequência ativa");
        if (expectedGuid !== undefined) {
            var guidError = dc__requireGuid(s, expectedGuid);
            if (guidError) return dc__err(guidError);
        }
        app.enableQE();
        var q = (typeof qe !== "undefined" && qe && qe.project) ? qe.project.getActiveSequence() : null;
        if (!q || !q.CTI) return dc__err("o Premiere ainda não liberou a exportação de quadro (QE); tente de novo");
        var ticks = String(Math.round(Number(us) / 1e6 * DC_TICKS));
        var previous = s.getPlayerPosition().ticks;
        var hidden = [], owned = dc__owned(previousClips || []);
        var tc;
        try {
            // O fundo da prévia não pode trazer a legenda antiga já aplicada.
            // Desliga só clipes reconhecidos pelo manifesto e restaura seu estado.
            for (var ti = 0; ti < s.videoTracks.numTracks; ti++) {
                var track = s.videoTracks[ti];
                for (var ci = 0; ci < track.clips.numItems; ci++) {
                    var clip = track.clips[ci];
                    if (dc__isOwned(clip, owned)) {
                        hidden.push({ clip: clip, disabled: clip.disabled });
                        clip.disabled = true;
                    }
                }
            }
            s.setPlayerPosition(ticks);
            tc = q.CTI.timecode;
            if (!tc) return dc__err("o Premiere não informou o timecode do quadro");
            var f = new File(outPath);
            if (f.exists) f.remove();
            q.exportFramePNG(tc, outPath.replace(/\.png$/i, ""));
        } finally {
            for (var hi = 0; hi < hidden.length; hi++) hidden[hi].clip.disabled = hidden[hi].disabled;
            s.setPlayerPosition(previous);
        }
        var png = new File(outPath);
        if (!png.exists) return dc__err("o Premiere não exportou o quadro");
        return dc__ok({ path: outPath, timecode: tc });
    } catch (e) { return dc__err(e); }
}

/* Presets de áudio WAV que vêm com o próprio Premiere instalado nesta máquina. */
function dc_findAudioPresets() {
    try {
        var out = [];
        function walk(f, d) {
            if (d > 6 || out.length > 10) return;
            var items = f.getFiles();
            if (!items) return;
            for (var i = 0; i < items.length; i++) {
                var it = items[i];
                if (it instanceof Folder) walk(it, d + 1);
                else {
                    var n = it.name.toLowerCase();
                    if (n.indexOf(".epr") > 0 && (n.indexOf("waveform") >= 0 || n.indexOf("wav") >= 0)) out.push(it.fsName);
                }
            }
        }
        walk(new Folder(Folder.appPackage.fsName + "/MediaIO/systempresets"), 0);
        out.sort(function (a, b) { return (b.indexOf("48kHz 16") >= 0) - (a.indexOf("48kHz 16") >= 0); });
        return dc__ok({ presets: out, app: Folder.appPackage.fsName });
    } catch (e) { return dc__err(e); }
}
