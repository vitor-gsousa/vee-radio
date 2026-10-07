// Torna o comando mais rápido: os formulários e os links são enviados com fetch
// e só se troca o que mudou. O servidor continua a fazer o trabalho; aqui só se
// pede, troca e mostra.
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
        if (target && target.classList.contains("modal")) target.classList.add("open");
    }

    // A página da playlist com o filtro e a ordenação que estão no URL
    function listUrl() {
        var current = new URLSearchParams(location.search);
        var params = new URLSearchParams();
        ["nome", "filtro", "ordenar"].forEach(function (key) {
            if (current.get(key)) params.set(key, current.get(key));
        });
        var query = params.toString();
        return location.pathname + (query ? "?" + query : "");
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
    // o telemóvel nem para quem usa o mesmo link). Repõem-se nas páginas de Descobrir
    // que não os trazem no URL; nas de resultados valem os do URL
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

    var DISCOVER_PAGES = ["/descobrir", "/descobrir/temas", "/descobrir/paises"];

    function restoreFilters() {
        if (DISCOVER_PAGES.indexOf(location.pathname) === -1) return;
        var saved = savedFilters();
        var inUrl = new URLSearchParams(location.search);
        document.querySelectorAll(".filters select").forEach(function (sel) {
            if (sel.name === "para" || inUrl.has(sel.name)) return;
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
        // Pesquisa ou filtros de um tema: os resultados ficam no mesmo sítio
        var results = (form.matches(".search-form") || form.matches(".refine")) && document.querySelector(".results");
        if (results) {
            var before = results.innerHTML;
            results.innerHTML = skeletonRows(6);
            return function () { results.innerHTML = before; };
        }
        // Escolher um tema ou um país: a página das rádios ainda não existe, por
        // isso a página atual passa a um esqueleto com o nome escolhido
        var main = document.querySelector(".main");
        if (!main || !submitter) return null;
        var page = main.innerHTML;
        main.innerHTML = '<header class="page-head"><div class="page-head-text"><h1></h1>' +
            '<p class="muted">A procurar rádios…</p></div></header>' + skeletonRows(8);
        main.querySelector("h1").textContent = submitter.textContent.trim();
        window.scrollTo(0, 0);
        return function () { main.innerHTML = page; };
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

    // Mostra o elemento do #fragmento (por exemplo, #e12, a estação a tocar) e acende-o
    function revealHash() {
        var id = decodeURIComponent(location.hash.slice(1));
        var el = id && document.getElementById(id);
        if (!el || el.classList.contains("modal")) return false;
        var still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        el.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
        if (el.classList.contains("tile")) {
            el.classList.remove("flash");
            void el.offsetWidth;
            el.classList.add("flash");
            setTimeout(function () { el.classList.remove("flash"); }, 1300);
        }
        return true;
    }

    // Troca o conteúdo da página pelo de uma resposta HTML. Na mesma página mantém
    // o scroll da página e o da janela aberta (por exemplo, depois de juntar uma
    // rádio a partir dos resultados); noutra página começa no topo
    function swap(html, url, push) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var modal = openModal();
        var box = modal && modal.querySelector(".modal-box");
        var keep = box && !push && { id: modal.id, top: box.scrollTop };
        var focus = saveFocus();
        var y = window.scrollY;
        var samePage = !url || new URL(url, location.href).pathname === location.pathname;
        document.title = doc.title;
        document.body.innerHTML = doc.body.innerHTML;
        if (url) {
            history[push ? "pushState" : "replaceState"](null, "", url);
            currentPath = location.pathname + location.search;
        }
        syncModals();
        window.scrollTo(0, samePage ? y : 0);
        if (!samePage) revealHash();
        modal = openModal();
        if (keep && modal && modal.id === keep.id) modal.querySelector(".modal-box").scrollTop = keep.top;
        markLoadedImages();
        restoreFocus(focus);
        updateTitle();
        scheduleUndo();
        restoreFilters();
        updateSelection();
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
        var next = (tile || form.closest(".undo") || form.id === "editar") && form.querySelector('input[name="next"]');
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
                var loaded = {};
                player.querySelectorAll("img.loaded").forEach(function (img) { loaded[img.getAttribute("src")] = true; });
                fresh.querySelectorAll("img").forEach(function (img) {
                    if (loaded[img.getAttribute("src")]) img.classList.add("loaded");
                });
                player.replaceWith(fresh);
                // O reprodutor aberto (#tocar) vem dentro da barra nova: continua aberto
                syncModals();
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
        // aviso de erro: recarrega a página, mas nunca com uma janela aberta nem a
        // meio de uma seleção. Só na página da playlist que está a tocar
        var list = document.getElementById("estacoes");
        var changed = list && (list.dataset.queue !== String(state.queue) ||
            !!state.error !== !!document.getElementById("aviso"));
        // No Início, o cartão "A tocar agora" é da página: muda-se quando a estação muda
        var panel = document.getElementById("painel");
        if (panel && panel.dataset.now !== (state.id == null ? "" : String(state.id))) changed = true;
        if (changed && !openModal() && !busy && !selecting()) load(location.pathname + location.search + location.hash, false);
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

    // Selecionar liga e desliga o modo; o ✕ da barra sai dele e desmarca tudo
    document.addEventListener("click", function (e) {
        if (e.target.closest(".select-toggle")) setSelecting(!selecting());
        else if (e.target.closest(".edit-clear")) setSelecting(false);
    });

    // "Mostrar na lista" no reprodutor aberto: na página da lista que está a tocar
    // fecha-o, desliza até ao cartão e acende-o; noutra página vai lá (e o swap mostra o cartão)
    document.addEventListener("click", function (e) {
        var go = e.target.closest("a.show-in-list");
        var tile = go && document.getElementById(go.getAttribute("href").split("#")[1]);
        if (!tile) return;
        e.preventDefault();
        location.hash = "fechar";
        if (tile.hidden) return toast("A estação a tocar está escondida pelo filtro da lista.");
        history.replaceState(null, "", location.pathname + location.search + "#" + tile.id);
        revealHash();
    });

    // Navegação entre páginas sem recarregar tudo: o script pede a página e troca o
    // conteúdo, como nos formulários. Os links que só mudam o # (as janelas) ficam
    // com o browser, e os de outros sites, ficheiros e logótipos também
    document.addEventListener("click", function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest("a[href]");
        if (!a || a.target || a.hasAttribute("download")) return;
        var url = new URL(a.href, location.href);
        if (url.origin !== location.origin || /^\/(logo|static|entrar)(\/|$)/.test(url.pathname)) return;
        if (url.pathname === location.pathname && url.search === location.search) return;
        e.preventDefault();
        load(url.pathname + url.search + url.hash, true);
    });

    // Página de uma playlist, modo Selecionar: tocar num cartão escolhe-o, e a barra
    // por cima do reprodutor diz quantos estão escolhidos e o que fazer com eles
    function selecting() {
        var main = document.querySelector(".main");
        return !!(main && main.classList.contains("selecting"));
    }

    function setSelecting(on) {
        var main = document.querySelector(".main");
        if (!main || !document.getElementById("editar")) return;
        main.classList.toggle("selecting", on);
        if (!on) document.querySelectorAll('input[form="editar"]:checked').forEach(function (box) { box.checked = false; });
        var toggle = document.querySelector(".select-toggle");
        if (toggle) {
            toggle.setAttribute("aria-pressed", on ? "true" : "false");
            toggle.querySelector("span").textContent = on ? "Cancelar" : "Selecionar";
        }
        updateSelection();
    }

    function updateSelection() {
        var panel = document.getElementById("editar");
        if (!panel) return;
        var n = document.querySelectorAll('input[form="editar"]:checked').length;
        panel.querySelector(".edit-count").textContent = n === 0 ? "Toca nas estações para as escolher" :
            n + (n === 1 ? " estação escolhida" : " estações escolhidas");
        panel.querySelectorAll("button[name=acao]").forEach(function (b) { b.disabled = n === 0; });
    }

    document.addEventListener("input", function (e) {
        var t = e.target;
        var clear = t.parentNode && t.parentNode.querySelector(".search-clear");
        if (clear) clear.hidden = !t.value;
        if (t.matches(".list-tools input")) return filterList(t);
        // Procurar rádios enquanto se escreve
        if (t.matches('.search-form input[name="q"]')) {
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
        if (t.form && t.form.id === "editar" && t.type === "checkbox") return updateSelection();
        if (t.matches(".list-tools select")) return t.form.requestSubmit();
        if (!t.closest(".filters")) return;
        // Os filtros ficam lembrados para as outras páginas de Descobrir. A playlist de
        // "Juntar a" não: ao voltar, é a que está a tocar
        if (t.name !== "para") saveFilter(t.name, t.value);
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
        updateSelection();
        setInterval(poll, POLL_MS);
    });
})();
