// Candidate list: own grade saved on change (PUT /api/oceny/<id>), new comments added (POST /api/komentarze/<id>).
(() => {
  // the list restored from the back/forward cache predates the latest saves
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) location.reload();
  });

  function send(method, path, id, body) {
    return fetch(path + encodeURIComponent(id), {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    }).then((response) => {
      if (!response.ok) throw response;
      return response.json();
    });
  }

  function failure(error) {
    if (error.status === 401) return "Sesja wygasła. Odśwież stronę i zaloguj się ponownie.";
    if (error.status === 429) return "Za dużo zapisów naraz. Spróbuj ponownie za chwilę.";
    return "Nie udało się zapisać. Spróbuj ponownie.";
  }

  function show(status, message, error) {
    status.textContent = message;
    status.classList.toggle("error", Boolean(error));
  }

  function hhmm(iso) {
    return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  }

  document.querySelectorAll("select.ocena").forEach((select) => {
    const status = select.parentElement.querySelector(".saved");
    let saved = select.value;
    let inflight = Promise.resolve();
    select.addEventListener("change", () => {
      const wanted = select.value;
      show(status, "Zapisywanie…");
      // one save at a time, so the last choice is also the last write
      inflight = inflight
        .catch(() => {})
        .then(() => send("PUT", "/api/oceny/", select.dataset.id, { ocena: wanted === "" ? null : Number(wanted) }));
      inflight
        .then((data) => {
          saved = wanted;
          show(status, "Zapisano " + hhmm(data.saved_at));
        })
        .catch((error) => {
          if (select.value === wanted) select.value = saved;
          show(status, failure(error), true);
        });
    });
  });

  document.querySelectorAll("form.add-comment").forEach((form) => {
    const text = form.querySelector("textarea");
    const button = form.querySelector("button");
    const status = form.querySelector(".saved");
    const details = form.closest("details");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const komentarz = text.value.trim();
      if (!komentarz) return;
      button.disabled = true;
      show(status, "Zapisywanie…");
      send("POST", "/api/komentarze/", form.dataset.id, { komentarz: komentarz })
        .then((data) => {
          const item = document.createElement("li");
          item.textContent = komentarz;
          details.querySelector("ul.comments").append(item);
          details.querySelector(".none")?.remove();
          text.value = "";
          show(status, "Dodano " + hhmm(data.saved_at));
        })
        .catch((error) => show(status, failure(error), true))
        .finally(() => {
          button.disabled = false;
        });
    });
  });
})();
