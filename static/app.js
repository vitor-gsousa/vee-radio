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
    // O mesmo para o estado do reprodutor: o /estado do polling pedido antes de um
    // clique (parar, mudar de estação) não pode chegar depois e desfazê-lo na barra
    var stateSeq = 0;
    var searchTimer = null;

    function setBusy(on) {
        busy += on ? 1 : -1;
        document.body.classList.toggle("busy", busy > 0);
    }

    // Mensagem curta por cima da barra: vermelha para erros, verde com ok
    function toast(text, ok) {
        var old = document.querySelector(".toast");
        if (old) old.remove();
        var el = document.createElement("div");
        el.className = ok ? "toast ok" : "toast";
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
        if (target && target.classList.contains("modal")) {
            target.classList.add("open");
        } else if (location.pathname === "/search" || location.pathname === "/tema" ||
                   location.pathname === "/playlist" || new URLSearchParams(location.search).has("para")) {
            // Fechou-se a janela da pesquisa, do tema ou de uma playlist: o URL volta a
            // ser o da lista, para as atualizações da página não voltarem a pedir tudo à
            // API, e "Juntar a" volta à playlist a tocar
            history.replaceState(null, "", listUrl() + location.hash);
            currentPath = location.pathname + location.search;
        }
    }

    // A lista de estações com o filtro e a ordenação que estão no URL
    function listUrl() {
        var current = new URLSearchParams(location.search);
        var params = new URLSearchParams();
        ["filtro", "ordenar"].forEach(function (key) {
            if (current.get(key)) params.set(key, current.get(key));
        });
        var query = params.toString();
        return "/" + (query ? "?" + query : "");
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

    // A faixa "Anular" depois do ✕ só vale uns minutos: esconde-se quando o tempo
    // acaba (sem script, desaparece na página seguinte)
    var undoTimer = null;
    function scheduleUndo() {
        clearTimeout(undoTimer);
        var bar = document.querySelector(".undo");
        var left = bar && parseInt(bar.dataset.left, 10);
        if (left > 0) undoTimer = setTimeout(function () { bar.remove(); }, left * 1000);
    }

    // Os filtros de Descobrir ficam lembrados neste browser (só aqui: não vão para
    // o telemóvel nem para quem usa o mesmo link). Repõem-se na página principal;
    // nas páginas de resultados os filtros são os do URL
    var FILTERS_KEY = "vee-radio-filtros";

    function savedFilters() {
        try {
            return JSON.parse(localStorage.getItem(FILTERS_KEY)) || {};
        } catch (e) {
            return {};
        }
    }

    function saveFilter(name, value) {
        var saved = savedFilters();
        saved[name] = value;
        try {
            localStorage.setItem(FILTERS_KEY, JSON.stringify(saved));
        } catch (e) {
            // Sem armazenamento (janela privada): os filtros ficam só nesta página
        }
    }

    function restoreFilters() {
        if (location.pathname !== "/") return;
        var saved = savedFilters();
        document.querySelectorAll("#juntar .filters select, #temas .filters select, #paises .filters select").forEach(function (sel) {
            var value = saved[sel.name];
            // Só valores que ainda existem na lista (um país retirado fica em "Todos")
            if (typeof value === "string" && Array.prototype.some.call(sel.options, function (o) { return o.value === value; })) {
                sel.value = value;
            }
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
        scheduleUndo();
        restoreFilters();
    }

    async function handle(response, url, push) {
        var next = response.headers.get("X-Location");
        if (next) return load(next, false);
        var type = response.headers.get("Content-Type") || "";
        if (type.indexOf("application/json") !== -1) return updatePlayer(await response.json());
        if (response.status === 204) return true;
        // Páginas de erro também chegam aqui e substituem a página, como sem script
        swap(await response.text(), response.ok ? url : null, push);
        // "A reiniciar": o cabeçalho Refresh só vale para a navegação normal, por
        // isso volta-se à página principal à mão quando passar o tempo indicado
        var refresh = parseInt(response.headers.get("Refresh") || "", 10);
        if (refresh > 0) setTimeout(function () { location.assign("/"); }, refresh * 1000);
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
        // Um segundo toque no mesmo cartão enquanto o primeiro não acabou é ignorado
        if (tile && tile.classList.contains("pending")) return;
        // O "next" dos cartões foi escrito com o URL de quando a página foi feita;
        // o filtro escrito entretanto só está no URL atual
        var next = (tile || form.closest(".undo")) && form.querySelector('input[name="next"]');
        if (next) next.value = listUrl();
        // "+ Juntar" no reprodutor volta à página que está aberta (por exemplo, aos
        // resultados, para mostrar o ✓), e não sempre a /
        var keep = form.closest(".player") && form.querySelector('input[name="next"]');
        if (keep) keep.value = location.pathname + location.search + location.hash;
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
        // A resposta de um /estado pedido antes deste envio é descartada
        stateSeq++;
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
        if (player && state.player) {
            var fresh = new DOMParser().parseFromString(state.player, "text/html").querySelector(".player");
            if (fresh && fresh.dataset.sig !== player.dataset.sig) {
                // O logótipo é o mesmo se a estação não mudou: não volta a brilhar
                var oldLogo = player.querySelector("img.player-logo.loaded");
                var newLogo = fresh.querySelector("img.player-logo");
                if (oldLogo && newLogo && oldLogo.getAttribute("src") === newLogo.getAttribute("src")) newLogo.classList.add("loaded");
                player.replaceWith(fresh);
            }
        }
        // Só o cartão que deixou de tocar e o que passou a tocar, e não todos a cada 5 s
        var was = document.querySelector(".tile.current");
        var now = state.id == null ? null : document.querySelector('.tile[data-id="' + CSS.escape(String(state.id)) + '"]');
        if (was !== now) {
            if (was) was.classList.remove("current");
            if (now) now.classList.add("current");
        }
        updateTitle(state.station, state.playing);
        if (state.notice) toast(state.notice.text, state.notice.ok);
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
        var seq = ++stateSeq;
        try {
            // Com o sig da barra atual: se nada mudou, o servidor não a manda outra vez
            var sig = document.querySelector(".player").dataset.sig || "";
            var response = await fetch("/estado?sig=" + encodeURIComponent(sig), { headers: HEADERS });
            // Entretanto carregou-se num botão: este estado é de antes e já não vale
            if (seq !== stateSeq) return;
            if (response.ok) updatePlayer(await response.json());
        } catch (e) {
            // Sem rede: tenta outra vez no próximo ciclo
        }
    }

    // Sem acentos nem maiúsculas, como o plain_text do servidor
    function plain(text) {
        return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
    }

    // Filtra a lista de estações enquanto se escreve. As escondidas já vêm na
    // página (com hidden), por isso não é preciso pedir nada ao servidor; o URL
    // fica com o filtro para as próximas atualizações da página o manterem
    function filterList(input) {
        var wanted = plain(input.value.trim());
        var shown = 0;
        document.querySelectorAll(".tile").forEach(function (t) {
            var match = (t.dataset.plain || "").indexOf(wanted) !== -1;
            t.hidden = !match;
            if (match) shown++;
        });
        var empty = document.getElementById("sem-resultados");
        if (empty) empty.hidden = shown > 0;
        var note = document.getElementById("nota-ordem");
        var order = input.form.querySelector('select[name="ordenar"]');
        if (note) note.hidden = !(input.value.trim() || (order && order.value));
        var url = new URL(location.href);
        if (input.value.trim()) url.searchParams.set("filtro", input.value.trim());
        else url.searchParams.delete("filtro");
        history.replaceState(null, "", url.pathname + url.search + url.hash);
        currentPath = location.pathname + location.search;
    }

    // O ✕ das caixas de pesquisa limpa a caixa em vez de seguir o link
    document.addEventListener("click", function (e) {
        var clear = e.target.closest(".search-clear");
        if (!clear) return;
        e.preventDefault();
        var input = clear.parentNode.querySelector("input");
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
    });

    // Tocar na estação do reprodutor: desliza até ao cartão e acende-o, sem
    // deixar #e<id> no URL (sem script, o link salta para o cartão)
    document.addEventListener("click", function (e) {
        var go = e.target.closest("a.player-go");
        var tile = go && document.getElementById(go.getAttribute("href").slice(1));
        if (!tile) return;
        e.preventDefault();
        if (tile.hidden) return toast("A estação a tocar está escondida pelo filtro da lista.");
        var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        tile.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
        tile.classList.remove("flash");
        void tile.offsetWidth;
        tile.classList.add("flash");
        setTimeout(function () { tile.classList.remove("flash"); }, 1300);
    });

    document.addEventListener("input", function (e) {
        var t = e.target;
        var clear = t.parentNode && t.parentNode.querySelector(".search-clear");
        if (clear) clear.hidden = !t.value;
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
    // Atualizar. Nos separadores Temas e Países os filtros só escolhem o que vem
    // depois de carregar num tema ou país, por isso aí não se envia nada
    document.addEventListener("change", function (e) {
        var t = e.target;
        if (t.matches(".list-tools select")) return t.form.requestSubmit();
        if (!t.closest(".filters")) return;
        // Os separadores de Descobrir partilham os filtros: o que se escolhe num
        // vale nos outros (sem script, cada um fica com o que veio na página)
        if (t.closest("#juntar, #temas, #paises")) {
            document.querySelectorAll('#juntar, #temas, #paises').forEach(function (m) {
                var same = m.querySelector('.filters select[name="' + CSS.escape(t.name) + '"]');
                if (same && same !== t) same.value = t.value;
            });
            // A playlist de "Juntar a" não fica lembrada: ao voltar, é a que está a tocar
            if (t.name !== "para") saveFilter(t.name, t.value);
        }
        if (t.form.matches(".refine")) return t.form.requestSubmit();
        var query = t.form.querySelector('input[name="q"]');
        if (query && query.value.trim()) return t.form.requestSubmit();
        // Mudou "Juntar a" sem pesquisa: a página volta com os textos e o ✓ dessa playlist
        if (t.name === "para") {
            var url = new URL(location.href);
            if (t.value) url.searchParams.set("para", t.value);
            else url.searchParams.delete("para");
            load(url.pathname + url.search + url.hash, false);
        }
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
        scheduleUndo();
        restoreFilters();
        setInterval(poll, POLL_MS);
    });
})();
