// Grade autosave on the candidate card: debounced PUT /api/oceny/<id>, flushed when leaving the page.
(() => {
  const box = document.getElementById("grade");
  if (!box) {
    // the list restored from the back/forward cache predates the latest saves
    window.addEventListener("pageshow", (event) => {
      if (event.persisted) location.reload();
    });
    return;
  }
  const DELAY = 1500;
  const url = "/api/oceny/" + encodeURIComponent(box.dataset.id);
  const text = document.getElementById("komentarz");
  const status = document.getElementById("status");
  const scale = box.querySelectorAll("button[data-ocena]");
  let ocena = box.dataset.ocena === "" ? null : Number(box.dataset.ocena);
  let timer = null;
  let dirty = false;
  let saving = false;
  let pending = false;
  let resumeAt = 0; // after a 429: no save before this time, except the keepalive flush
  let inflight = Promise.resolve();

  function show(message, error) {
    status.textContent = message;
    status.classList.toggle("error", Boolean(error));
  }

  function hhmm(iso) {
    return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  }

  function render() {
    scale.forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.ocena) === ocena)));
  }

  function save(keepalive) {
    clearTimeout(timer);
    timer = null;
    if (!dirty) return;
    if (resumeAt > Date.now() && !keepalive) {
      timer = setTimeout(save, resumeAt - Date.now());
      return;
    }
    if (saving && !keepalive) {
      pending = true;
      return;
    }
    dirty = false;
    saving = true;
    show("Zapisywanie…");
    inflight = fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ocena: ocena, komentarz: text.value }),
      credentials: "same-origin",
      keepalive: Boolean(keepalive),
    })
      .then((response) => {
        if (!response.ok) throw response;
        return response.json();
      })
      .then((data) => show("Zapisano " + hhmm(data.saved_at)))
      .catch((error) => {
        dirty = true;
        if (error.status === 429) {
          resumeAt = Date.now() + 1000 * (Number(error.headers.get("Retry-After")) || 5);
          clearTimeout(timer);
          timer = setTimeout(save, resumeAt - Date.now());
          show("Za dużo zapisów naraz. Zapiszę za chwilę…");
          return;
        }
        show(error.status === 401 ? "Sesja wygasła. Odśwież stronę i zaloguj się ponownie."
                                  : "Nie udało się zapisać. Zmiany zapiszą się przy następnej edycji.", true);
      })
      .finally(() => {
        saving = false;
        if (pending) {
          pending = false;
          save();
        }
      });
  }

  // The next page is rendered before pagehide fires, so a link or logout waits for the save to finish
  function leave(go) {
    save();
    const wait = () => (saving ? inflight.then(wait) : go());
    wait();
  }

  function changed() {
    dirty = true;
    clearTimeout(timer);
    timer = setTimeout(save, DELAY);
  }

  box.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    ocena = "clear" in button.dataset ? null : Number(button.dataset.ocena);
    render();
    changed();
  });
  text.addEventListener("input", changed);
  text.addEventListener("blur", () => save());
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || link.target || event.ctrlKey || event.metaKey || event.shiftKey || !(dirty || saving)) return;
    event.preventDefault();
    leave(() => location.assign(link.href));
  });
  document.addEventListener("submit", (event) => {
    if (!(dirty || saving)) return;
    event.preventDefault();
    leave(() => event.target.submit());
  });
  window.addEventListener("pagehide", () => save(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") save(true);
  });
})();
