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

  /* ──────────────────────────────────────────────────────────
   * uploadFlow — máquina de estados da página de upload.
   * Estados: idle → uploading → polling | error
   * Em sucesso, deixa o HTMX (no fragmento de polling) renderizar
   * o estado final. Em erro de upload (pre-job), mostra mensagem.
   *
   * Parametrizado: passe `(endpoint, pollEndpoint)` para reutilizar
   * a mesma máquina em /catalogs/upload e /orders/upload. Defaults
   * apontam para catálogos por compat — `x-data="uploadFlow"` sem
   * args continua funcionando.
   * ────────────────────────────────────────────────────────── */
  // eslint-disable-next-line no-undef
  Alpine.data("uploadFlow", (
    endpoint = "/catalogs/upload",
    pollEndpoint = "/catalogs/upload/poll/",
  ) => ({
    endpoint,
    pollEndpoint,
    state: "idle",
    pct: 0,
    jobId: null,
    selectedFile: null,
    selectedFileName: "",
    error: null,
    dragging: false,

    onFileChange(event) {
      const files = event.target.files;
      if (files && files.length > 0) {
        this.selectedFile = files[0];
        this.selectedFileName = files[0].name;
      }
    },

    onDrop(event) {
      this.dragging = false;
      const files = event.dataTransfer?.files;
      if (files && files.length > 0) {
        this.selectedFile = files[0];
        this.selectedFileName = files[0].name;
        // Atualiza o <input type="file"> também — alguns navegadores
        // exigem que ele tenha o arquivo para o submit nativo funcionar.
        const input = this.$root.querySelector('input[type="file"]');
        if (input) {
          const dt = new DataTransfer();
          dt.items.add(files[0]);
          input.files = dt.files;
        }
      }
    },

    async submit(event) {
      event.preventDefault();
      this.error = null;
      const form = event.target;
      const fd = new FormData(form);
      if (!fd.get("file") || (this.selectedFile == null && !fd.get("file").size)) {
        this.error = "Selecione um arquivo PDF.";
        return;
      }
      this.state = "uploading";
      this.pct = 0;
      try {
        const resp = await window.uploadProgress(this.endpoint, fd, (p) => {
          this.pct = p;
        });
        this.jobId = resp.job_id;
        this.state = "polling";
        // Aguarda o Alpine re-renderizar o bloco de polling e então
        // instrui o HTMX a processar o div (aplicar hx-get + hx-trigger).
        this.$nextTick(() => {
          const target = document.getElementById("upload-poll-target");
          if (target && window.htmx) {
            target.setAttribute(
              "hx-get",
              this.pollEndpoint + this.jobId,
            );
            target.setAttribute("hx-trigger", "load, every 2s");
            target.setAttribute("hx-swap", "innerHTML");
            window.htmx.process(target);
            window.htmx.trigger(target, "load");
          }
        });
      } catch (err) {
        // err: { status, body: { success:false, error:{code,message,...}} }
        const code = err?.body?.error?.code || "UNKNOWN";
        const friendly = this._friendlyMessage(code, err?.body?.error?.message);
        this.error = friendly;
        this.state = "error";
      }
    },

    _friendlyMessage(code, fallback) {
      const map = {
        FILE_TOO_LARGE: "Arquivo maior que 50 MB.",
        PDF_ENCRYPTED: "PDF protegido com senha.",
        INVALID_FILE_TYPE: "O arquivo não é um PDF válido.",
        PDF_NO_PRODUCTS: "Nenhum produto detectado no catálogo.",
        PDF_CORRUPT: "Arquivo PDF inválido ou corrompido.",
        PDF_FLATTENED: "PDF foi achatado (impresso). Reenvie o original editável.",
      };
      return map[code] || fallback || "Não foi possível enviar o arquivo.";
    },

    reset() {
      this.state = "idle";
      this.pct = 0;
      this.jobId = null;
      this.error = null;
      this.selectedFile = null;
      this.selectedFileName = "";
    },
  }));
});
