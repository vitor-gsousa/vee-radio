// Melhora o comando sem o tornar dependente de JavaScript: os formulários e os
// links continuam a ser os mesmos, mas são enviados com fetch e só se troca o
// que mudou. Sem este ficheiro, tudo funciona com redirects e :target.
(function () {
    "use strict";

    var POLL_MS = 5000;
    var HEADERS = { "X-Requested-With": "fetch" };
    // Caminho (sem #) da página que está no ecrã, para distinguir o "voltar" que
    // só fecha ou abre uma janela do que muda de página
    var currentPath = location.pathname + location.search;
    var busy = 0;

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

    // Troca o conteúdo da página pelo de uma resposta HTML, mantendo o scroll da
    // página e o da janela aberta, se for a mesma (por exemplo, depois de juntar
    // uma rádio a partir dos resultados da pesquisa)
    function swap(html, url, push) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var modal = openModal();
        var box = modal && modal.querySelector(".modal-box");
        var keep = box && { id: modal.id, top: box.scrollTop };
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
        updateTitle();
    }

    async function handle(response, url, push) {
        var next = response.headers.get("X-Location");
        if (next) return load(next, false);
        var type = response.headers.get("Content-Type") || "";
        if (type.indexOf("application/json") !== -1) return updatePlayer(await response.json());
        if (response.status === 204) return;
        // Páginas de erro também chegam aqui e substituem a página, como sem script
        swap(await response.text(), response.ok ? url : null, push);
    }

    async function load(url, push) {
        setBusy(true);
        try {
            await handle(await fetch(url, { headers: HEADERS }), url, push);
        } catch (e) {
            toast("Sem ligação ao telemóvel. Tenta outra vez.");
        } finally {
            setBusy(false);
        }
    }

    async function submit(form, submitter) {
        var data = new FormData(form);
        if (submitter && submitter.name) data.append(submitter.name, submitter.value);
        var action = new URL(form.getAttribute("action") || location.href, location.href);
        var tile = form.closest(".tile");
        if (tile) tile.classList.add("pending");
        if ((form.getAttribute("method") || "get").toLowerCase() === "get") {
            // Os filtros deixados em "Todos" não precisam de ir no URL
            var params = new URLSearchParams();
            data.forEach(function (value, key) { if (value !== "") params.append(key, value); });
            action.search = params.toString();
            return load(action.pathname + action.search + action.hash, true);
        }
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
            if (fresh && fresh.dataset.sig !== player.dataset.sig) player.replaceWith(fresh);
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

    document.addEventListener("submit", function (e) {
        e.preventDefault();
        submit(e.target, e.submitter);
    });

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
        updateTitle();
        setInterval(poll, POLL_MS);
    });
})();
