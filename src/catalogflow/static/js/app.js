/* ──────────────────────────────────────────────────────────────
 * CatalogFlow — Alpine.js helpers para a UI da gerente comercial.
 * Carregado em base.html depois de Alpine + HTMX. Sem build step.
 * ────────────────────────────────────────────────────────────── */

/**
 * Faz upload de um FormData para `endpoint` e reporta progresso via callback.
 *
 * Por que XMLHttpRequest em vez de fetch:
 * o `fetch()` não expõe progresso de upload (a streams API ainda é parcial
 * em alguns navegadores). XHR continua sendo a forma canônica de monitorar
 * `progress.loaded` / `progress.total` durante o envio de um arquivo grande.
 *
 * @param {string}   endpoint   URL para POST (ex.: "/catalogs/upload").
 * @param {FormData} formData   payload com o(s) arquivo(s) e campos.
 * @param {(pct:number)=>void} onProgress  callback chamado com 0..100.
 * @returns {Promise<object>}   resposta JSON parseada do servidor.
 *                              Resolve em status 2xx; rejeita caso contrário.
 */
window.uploadProgress = function uploadProgress(endpoint, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", endpoint);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && typeof onProgress === "function") {
        const pct = Math.round((event.loaded / event.total) * 100);
        onProgress(pct);
      }
    };

    xhr.onload = () => {
      let payload = null;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch (_err) {
        payload = { raw: xhr.responseText };
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
      } else {
        reject({ status: xhr.status, body: payload });
      }
    };

    xhr.onerror = () => reject({ status: 0, body: null });
    xhr.send(formData);
  });
};

/* ──────────────────────────────────────────────────────────────
 * Toast store — feedback global (canto superior direito).
 * Uso no template:
 *   $store.toasts.show("Catálogo enviado", "success")
 *   $store.toasts.show("Falhou", "error")
 * ────────────────────────────────────────────────────────────── */

document.addEventListener("alpine:init", () => {
  // eslint-disable-next-line no-undef
  Alpine.store("toasts", {
    items: /** @type {{id:number, kind:string, message:string}[]} */ ([]),
    _next: 1,

    show(message, kind = "info", timeoutMs = 4000) {
      const id = this._next++;
      this.items.push({ id, kind, message });
      // Toasts de erro persistem até o usuário fechar.
      if (kind !== "error" && timeoutMs > 0) {
        setTimeout(() => this.hide(id), timeoutMs);
      }
      return id;
    },

    hide(id) {
      this.items = this.items.filter((t) => t.id !== id);
    },
  });
});
