// Melhora o comando sem o tornar dependente de JavaScript: os formulários e os
// links continuam a ser os mesmos, mas são enviados com fetch e só se troca o
// que mudou. Sem este ficheiro, tudo funciona com redirects e :target.
(function () {
    "use strict";

    var POLL_MS = 5000;
    // Pesquisa enquanto se escreve: espera que se pare de escrever e por um mínimo de letras
    var SEARCH_DELAY_MS = 700;
    var SEARCH_MIN_CHARS = 3;
    var HEADERS = { "X-Requested-With": "fetch" };
    // Caminho (sem #) da página que está no ecrã, para distinguir o "voltar" que
    // só fecha ou abre uma janela do que muda de página
    var currentPath = location.pathname + location.search;
    var busy = 0;
    // Cada pedido de página tem um número; uma resposta que chega depois de um
    // pedido mais recente (por exemplo, ao escrever na pesquisa) é ignorada
    var loadSeq = 0;
    var searchTimer = null;

    function setBusy(on) {
        busy += on ? 1 : -1;
        document.body.classList.toggle("busy", busy > 0);
    }

    function toast(text) {
        var old = document.querySelector(".toast");
        if (old) old.remove();
        var el = document.createElement("div");
        el.className = "toast";
        el.textContent = text;
        document.body.appendChild(el);
        setTimeout(function () { el.remove(); }, 4000);
    }

    // As janelas abrem com :target, mas o :target não acompanha o history.pushState
    // nem os elementos trocados; a classe .open faz o mesmo papel
    function syncModals() {
        var id = decodeURIComponent(location.hash.slice(1));
        var target = id && document.getElementById(id);
        document.querySelectorAll(".modal.open").forEach(function (m) {
            if (m !== target) m.classList.remove("open");
        });
        if (target && target.classList.contains("modal")) target.classList.add("open");
    }

    function openModal() {
        return document.querySelector(".modal.open, .modal:target");
    }

    // Logótipos e ícones: brilham (esqueleto) até a imagem chegar. O evento load
    // não sobe na árvore, por isso é apanhado na fase de captura; as imagens que
    // já tinham chegado antes do script são marcadas por markLoadedImages
    function markLoaded(e) {
        if (e.target.tagName === "IMG") e.target.classList.add("loaded");
    }

    function markLoadedImages() {
        document.querySelectorAll("img:not(.loaded)").forEach(function (img) {
            if (img.complete) img.classList.add("loaded");
        });
    }

    // Blocos a brilhar com a forma dos resultados, enquanto se espera pela API
    function skeletonRows(count) {
        var row = '<div class="skel-row"><span class="skel skel-thumb"></span><div class="skel-body">' +
            '<span class="skel skel-line"></span><span class="skel skel-meta"></span></div></div>';
        return new Array(count + 1).join(row);
    }

    // Mostra um esqueleto onde a resposta vai aparecer e devolve uma função que
    // repõe o que lá estava, se o pedido falhar
    function showLoading(form, submitter) {
        if (form.matches(".list-tools")) {
            var stations = document.querySelector(".stations");
            if (!stations) return null;
            stations.classList.add("is-loading");
            return function () { stations.classList.remove("is-loading"); };
        }
        var box = form.closest(".modal-box");
        var results = box && box.querySelector(".results");
        if (results) {
            var before = results.innerHTML;
            results.innerHTML = skeletonRows(6);
            return function () { results.innerHTML = before; };
        }
        // Escolher um tema ou um país: a janela com as rádios ainda não existe,
        // por isso abre-se uma provisória com o nome escolhido
        var modal = form.closest(".modal");
        if (!modal || !submitter) return null;
        var loading = document.createElement("div");
        loading.className = "modal open";
        loading.innerHTML = '<a class="backdrop" href="#fechar" aria-label="Fechar"></a><div class="modal-box">' +
            '<h3></h3><p class="meta">A procurar rádios…</p>' + skeletonRows(6) + "</div>";
        loading.querySelector("h3").textContent = submitter.textContent.trim();
        document.body.appendChild(loading);
        return function () { loading.remove(); };
    }

    // Guarda o campo de texto onde se está a escrever, para continuar nele depois
    // de a página ser trocada (a pesquisa corre enquanto se escreve)
    function saveFocus() {
        var el = document.activeElement;
        if (!el || !el.form || !el.name || el.type === "hidden" || el.tagName !== "INPUT") return null;
        return { action: el.form.getAttribute("action"), name: el.name, value: el.value,
                 start: el.selectionStart, end: el.selectionEnd };
    }

    function restoreFocus(saved) {
        if (!saved) return;
        var el = document.querySelector('form[action="' + CSS.escape(saved.action) + '"] [name="' + CSS.escape(saved.name) + '"]');
        if (!el) return;
        el.focus();
        if (el.value !== saved.value) {
            // Escreveu-se mais enquanto se esperava: fica o texto novo e procura-se outra vez
            el.value = saved.value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
        }
        try {
            el.setSelectionRange(saved.start, saved.end);
        } catch (e) {
            // Alguns tipos de campo não têm seleção
        }
    }

    // Troca o conteúdo da página pelo de uma resposta HTML. Mantém o scroll da
    // página e, quando não é uma página nova, também o da janela aberta (por
    // exemplo, depois de juntar uma rádio a partir dos resultados da pesquisa)
    function swap(html, url, push) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var modal = openModal();
        var box = modal && modal.querySelector(".modal-box");
        var keep = box && !push && { id: modal.id, top: box.scrollTop };
        var focus = saveFocus();
        var y = window.scrollY;
        document.title = doc.title;
        document.body.innerHTML = doc.body.innerHTML;
        if (url) {
            history[push ? "pushState" : "replaceState"](null, "", url);
            currentPath = location.pathname + location.search;
        }
        syncModals();
        window.scrollTo(0, y);
        modal = openModal();
        if (keep && modal && modal.id === keep.id) modal.querySelector(".modal-box").scrollTop = keep.top;
        markLoadedImages();
        restoreFocus(focus);
        updateTitle();
    }

    async function handle(response, url, push) {
        var next = response.headers.get("X-Location");
        if (next) return load(next, false);
        var type = response.headers.get("Content-Type") || "";
        if (type.indexOf("application/json") !== -1) return updatePlayer(await response.json());
        if (response.status === 204) return true;
        // Páginas de erro também chegam aqui e substituem a página, como sem script
        swap(await response.text(), response.ok ? url : null, push);
        return true;
    }

    // Pede uma página e troca-a; devolve false se não houver rede
    async function load(url, push) {
        var seq = ++loadSeq;
        setBusy(true);
        try {
            var response = await fetch(url, { headers: HEADERS });
            if (seq !== loadSeq) return true;
            return await handle(response, url, push);
        } catch (e) {
            if (seq === loadSeq) toast("Sem ligação ao telemóvel. Tenta outra vez.");
            return false;
        } finally {
            setBusy(false);
        }
    }

    async function submit(form, submitter) {
        var tile = form.closest(".tile");
        // O "next" dos cartões foi escrito com o URL de quando a página foi feita;
        // o filtro escrito entretanto só está no URL atual
        var next = tile && form.querySelector('input[name="next"]');
        if (next) next.value = location.pathname + location.search;
        var data = new FormData(form);
        if (submitter && submitter.name) data.append(submitter.name, submitter.value);
        var action = new URL(form.getAttribute("action") || location.href, location.href);
        if ((form.getAttribute("method") || "get").toLowerCase() === "get") {
            clearTimeout(searchTimer);
            // Os filtros deixados em "Todos" não precisam de ir no URL
            var params = new URLSearchParams();
            data.forEach(function (value, key) { if (value !== "") params.append(key, value); });
            action.search = params.toString();
            var undo = showLoading(form, submitter);
            if (!(await load(action.pathname + action.search + action.hash, true)) && undo) undo();
            return;
        }
        if (tile) tile.classList.add("pending");
        setBusy(true);
        try {
            await handle(await fetch(action.pathname + action.search, { method: "POST", body: data, headers: HEADERS }));
        } catch (e) {
            toast("Sem ligação ao telemóvel. Tenta outra vez.");
        } finally {
            if (tile) tile.classList.remove("pending");
            setBusy(false);
        }
    }

    function updateTitle(station, playing) {
        if (station === undefined) {
            var el = document.querySelector(".player-station");
            var stop = document.querySelector('.player form[action="/play"]');
            station = el && !stop ? el.textContent : "";
            playing = !stop;
        }
        document.title = playing && station ? "▶ " + station : "Comando Rádio";
    }

    // Atualiza a barra do reprodutor e a estação marcada a partir do /estado
    function updatePlayer(state) {
        var player = document.querySelector(".player");
        if (player) {
            var fresh = new DOMParser().parseFromString(state.player, "text/html").querySelector(".player");
            if (fresh && fresh.dataset.sig !== player.dataset.sig) {
                // O logótipo é o mesmo se a estação não mudou: não volta a brilhar
                var oldLogo = player.querySelector("img.player-logo.loaded");
                var newLogo = fresh.querySelector("img.player-logo");
                if (oldLogo && newLogo && oldLogo.getAttribute("src") === newLogo.getAttribute("src")) newLogo.classList.add("loaded");
                player.replaceWith(fresh);
            }
        }
        document.querySelectorAll(".tile").forEach(function (t) {
            t.classList.toggle("current", t.dataset.pos === String(state.pos));
        });
        updateTitle(state.station, state.playing);
        // A lista de estações mudou noutro aparelho, ou apareceu ou desapareceu o
        // aviso de erro: recarrega a página, mas nunca com uma janela aberta
        var list = document.getElementById("estacoes");
        var changed = (list && list.dataset.queue !== String(state.queue)) ||
            !!state.error !== !!document.getElementById("aviso");
        if (changed && !openModal() && !busy) load(location.pathname + location.search + location.hash, false);
        return true;
    }

    async function poll() {
        if (document.hidden || busy || !document.querySelector(".player")) return;
        try {
            var response = await fetch("/estado", { headers: HEADERS });
            if (response.ok) updatePlayer(await response.json());
        } catch (e) {
            // Sem rede: tenta outra vez no próximo ciclo
        }
    }

    // Sem acentos nem maiúsculas, como o plain_text do servidor
    function plain(text) {
        return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    }

    // Filtra a lista de estações enquanto se escreve. As escondidas já vêm na
    // página (com hidden), por isso não é preciso pedir nada ao servidor; o URL
    // fica com o filtro para as próximas atualizações da página o manterem
    function filterList(input) {
        var wanted = plain(input.value.trim());
        var shown = 0;
        document.querySelectorAll(".tile").forEach(function (t) {
            var match = plain(t.dataset.name || "").indexOf(wanted) !== -1;
            t.hidden = !match;
            if (match) shown++;
        });
        var empty = document.getElementById("sem-resultados");
        if (empty) empty.hidden = shown > 0;
        var url = new URL(location.href);
        if (input.value.trim()) url.searchParams.set("filtro", input.value.trim());
        else url.searchParams.delete("filtro");
        history.replaceState(null, "", url.pathname + url.search + url.hash);
        currentPath = location.pathname + location.search;
    }

    document.addEventListener("input", function (e) {
        var t = e.target;
        if (t.matches(".list-tools input")) return filterList(t);
        // Procurar rádios enquanto se escreve
        if (t.matches('#juntar input[name="q"]')) {
            clearTimeout(searchTimer);
            if (t.value.trim().length >= SEARCH_MIN_CHARS) {
                searchTimer = setTimeout(function () { t.form.requestSubmit(); }, SEARCH_DELAY_MS);
            }
        }
    });

    // Mudar a ordenação da lista ou um filtro aplica-o logo, sem o botão OK ou
    // Atualizar. Nas janelas Temas e Países os filtros só escolhem o que vem
    // depois de carregar num tema ou país, por isso aí não se envia nada
    document.addEventListener("change", function (e) {
        var t = e.target;
        if (t.matches(".list-tools select")) return t.form.requestSubmit();
        if (!t.closest(".filters")) return;
        if (t.form.matches(".refine")) return t.form.requestSubmit();
        var query = t.form.querySelector('input[name="q"]');
        if (query && query.value.trim()) t.form.requestSubmit();
    });

    document.addEventListener("submit", function (e) {
        e.preventDefault();
        submit(e.target, e.submitter);
    });

    document.addEventListener("load", markLoaded, true);
    document.addEventListener("error", markLoaded, true);
    window.addEventListener("hashchange", syncModals);
    window.addEventListener("popstate", function () {
        if (location.pathname + location.search === currentPath) syncModals();
        else load(location.pathname + location.search + location.hash, false);
    });
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) poll();
    });

    document.addEventListener("DOMContentLoaded", function () {
        syncModals();
        markLoadedImages();
        updateTitle();
        setInterval(poll, POLL_MS);
    });
})();
