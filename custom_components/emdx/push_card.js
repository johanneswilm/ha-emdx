// Lovelace card: pick an image, downscale it in the browser, upload and
// push it to a Samsung EMDX e-paper panel. Each upload replaces the last.
// Shows a scaled preview of the last upload (GET /api/emdx/preview, authed).
//
// Defensive: setConfig never throws (a missing/renamed entity shows an
// in-card message instead of Lovelace's "Config error" card), and the
// upload result is checked for a real JSON response so a proxy/HTML error
// page cannot crash the card.
class EmdxPushCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this._root) {
      this._build();
    }
    if (!this._config.entity) {
      this._setStatus("No panel entity configured (set entity: switch.samsung_emdx_345r).");
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) {
      this._build();
    }
    if (this._config.preview !== false && !this._previewRequested) {
      this._previewRequested = true;
      this._loadPreview();
    }
  }

  getCardSize() {
    return this._config.preview === false ? 4 : 6;
  }

  _setStatus(text, error) {
    if (this._status) {
      this._status.textContent = text;
      this._status.style.color = error ? "var(--error-color)" : "";
    }
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    const card = document.createElement("ha-card");
    card.header = this._config.title || "Push image to panel";
    const body = document.createElement("div");
    body.style.padding = "16px";

    if (this._config.preview !== false) {
      const prev = document.createElement("div");
      prev.style.cssText =
        "display:none;margin:0 0 12px;text-align:center;";
      const img = document.createElement("img");
      img.alt = "Last uploaded image";
      img.style.cssText =
        "display:block;margin:0 auto;max-width:256px;max-height:160px;" +
        "width:100%;height:auto;border-radius:6px;";
      const cap = document.createElement("div");
      cap.textContent = "last upload";
      cap.style.cssText =
        "font-size:0.75rem;color:var(--secondary-text-color);margin-top:4px;";
      prev.appendChild(img);
      prev.appendChild(cap);
      body.appendChild(prev);
      this._preview = prev;
      this._previewImg = img;
    }

    const drop = document.createElement("div");
    drop.style.cssText =
      "border:2px dashed var(--primary-color);border-radius:8px;padding:24px;text-align:center;cursor:pointer;";
    const label = document.createElement("div");
    label.textContent = "Click to choose an image (replaces the last one)";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.style.display = "none";
    const status = document.createElement("div");
    status.style.cssText = "margin-top:8px;color:var(--secondary-text-color);";
    drop.addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      const file = input.files[0];
      this._upload(file, status);
      input.value = "";
    });
    drop.appendChild(label);
    drop.appendChild(input);
    drop.appendChild(status);
    body.appendChild(drop);
    card.appendChild(body);
    root.appendChild(card);
    this._root = root;
    this._status = status;
  }

  async _loadPreview() {
    if (!this._hass || !this._preview) {
      return;
    }
    try {
      const resp = await this._hass.fetchWithAuth("/api/emdx/preview", {
        cache: "no-store",
      });
      if (!resp.ok) {
        return;
      }
      const blob = await resp.blob();
      if (this._previewUrl) {
        URL.revokeObjectURL(this._previewUrl);
      }
      this._previewUrl = URL.createObjectURL(blob);
      this._previewImg.src = this._previewUrl;
      this._preview.style.display = "block";
    } catch (err) {
      // No last upload yet, or offline: keep the preview hidden.
    }
  }

  async _upload(file, status) {
    if (!file) {
      return;
    }
    if (!this._config.entity) {
      this._setStatus("No panel entity configured.", true);
      return;
    }
    this._setStatus("Processing…");
    try {
      const blob = await this._downscale(file);
      this._setStatus("Uploading and pushing…");
      const url =
        "/api/emdx/upload?entity_id=" +
        encodeURIComponent(this._config.entity);
      const resp = await this._hass.fetchWithAuth(url, {
        method: "POST",
        headers: { "Content-Type": "image/jpeg" },
        body: blob,
      });
      const data = await resp.json().catch(() => null);
      if (resp.ok && data) {
        this._setStatus(`Pushed ${(blob.size / 1024).toFixed(0)} kB ✓`);
        this._loadPreview();
      } else {
        this._setStatus((data && data.message) || `Error ${resp.status}`, true);
        // The upload view stores the file even when the push itself failed.
        this._loadPreview();
      }
    } catch (err) {
      this._setStatus(`Failed: ${err}`, true);
    }
  }

  _downscale(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const maxW = 2560;
        const maxH = 1440;
        const scale = Math.min(1, maxW / img.width, maxH / img.height);
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        canvas.toBlob(
          (b) => (b ? resolve(b) : reject(new Error("conversion failed"))),
          "image/jpeg",
          0.85
        );
        URL.revokeObjectURL(img.src);
      };
      img.onerror = () => reject(new Error("not a readable image"));
      img.src = URL.createObjectURL(file);
    });
  }
}

customElements.define("emdx-push-card", EmdxPushCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "emdx-push-card",
  name: "EMDX push card",
  description:
    "Upload an image and push it to a Samsung EMDX e-paper panel (replaces the last upload)",
});
