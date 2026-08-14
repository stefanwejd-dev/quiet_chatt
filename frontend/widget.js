/* Quiet Öppen Data — Chattwidget
 * Fristående JS-fil utan byggkedja (steg 14).
 * Inbäddas på quiet.nu med:
 *   <div id="quiet-widget" data-api="https://api.quiet.nu"></div>
 *   <script src="widget.js"></script>
 *
 * Kräver inga externa beroenden — CSP-tåligt, inga CDN, inga externa typsnitt.
 * Ljus/mörk tema via prefers-color-scheme.
 *
 * SSE-händelser från /fraga:
 *   stycke    {text, kallor: ["F1","F2"]}
 *   kallor    {kallor: [{id, etikett, myndighet, dataset, period, dimensioner,
 *              hamtad, lank_manniska, lank_maskin, licens, attribution,
 *              harledd, harledd_av}]}
 *   attribution {attribution: ["CC-BY-text …"]}
 *   forbehall {forbehall: "…"}
 *   svar      {kan_besvaras: false, forbehall: "…"}   (tomt register)
 *   fel       {meddelande: "…"}
 *   klart     {}
 */

(function () {
  "use strict";

  // -------------------------------------------------------------------------
  // CSS — injiceras som ett <style>-block en gång
  // -------------------------------------------------------------------------

  const CSS = `
:root {
  --qw-font: system-ui, -apple-system, "Segoe UI", sans-serif;
  --qw-mono: ui-monospace, "SF Mono", "Fira Code", monospace;

  --qw-radius: 10px;
  --qw-radius-sm: 6px;
  --qw-gap: 16px;
  --qw-gap-sm: 10px;

  /* Ljust tema */
  --qw-bg:         #f7f7f5;
  --qw-surface:    #ffffff;
  --qw-border:     #e4e2dc;
  --qw-shadow:     0 1px 3px rgba(0,0,0,.08), 0 4px 16px rgba(0,0,0,.05);
  --qw-text:       #1a1916;
  --qw-text-muted: #706e67;
  --qw-accent:     #1a6c4e;
  --qw-accent-bg:  #eaf5ef;
  --qw-fn-bg:      #f0ede8;
  --qw-input-bg:   #ffffff;
  --qw-note-bg:    #fdf8ec;
  --qw-note-border:#e8d9a0;
  --qw-note-text:  #6b5c20;
  --qw-error-bg:   #fdf2f2;
  --qw-error-text: #7a2020;
  --qw-derived-bg: #f3f0fc;
  --qw-derived-border: #c9bef0;
  --qw-derived-text: #4a3880;
}

@media (prefers-color-scheme: dark) {
  :root {
    --qw-bg:         #18181b;
    --qw-surface:    #222226;
    --qw-border:     #333339;
    --qw-shadow:     0 1px 3px rgba(0,0,0,.3), 0 4px 16px rgba(0,0,0,.2);
    --qw-text:       #e8e6e1;
    --qw-text-muted: #9b9890;
    --qw-accent:     #4aab7e;
    --qw-accent-bg:  #1a2e22;
    --qw-fn-bg:      #2a2a30;
    --qw-input-bg:   #2a2a30;
    --qw-note-bg:    #26240f;
    --qw-note-border:#5c4f18;
    --qw-note-text:  #c9b05a;
    --qw-error-bg:   #260f0f;
    --qw-error-text: #e08080;
    --qw-derived-bg: #1e1a2e;
    --qw-derived-border: #4a3880;
    --qw-derived-text: #b0a0e0;
  }
}

#quiet-widget * {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

#quiet-widget {
  font-family: var(--qw-font);
  font-size: 15px;
  line-height: 1.6;
  color: var(--qw-text);
  background: var(--qw-bg);
  border: 1px solid var(--qw-border);
  border-radius: var(--qw-radius);
  overflow: hidden;
  max-width: 760px;
  width: 100%;
}

/* ---- Formulär ---- */
.qw-form {
  display: flex;
  gap: var(--qw-gap-sm);
  padding: var(--qw-gap);
  background: var(--qw-surface);
  border-bottom: 1px solid var(--qw-border);
  align-items: flex-end;
}

.qw-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--qw-text-muted);
  margin-bottom: 6px;
}

.qw-input-wrap {
  flex: 1 1 0;
  min-width: 0;
}

.qw-input {
  width: 100%;
  padding: 10px 14px;
  font-family: var(--qw-font);
  font-size: 15px;
  line-height: 1.4;
  background: var(--qw-input-bg);
  border: 1px solid var(--qw-border);
  border-radius: var(--qw-radius-sm);
  color: var(--qw-text);
  resize: none;
  min-height: 44px;
  max-height: 140px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
  overflow-y: auto;
}

.qw-input:focus {
  border-color: var(--qw-accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--qw-accent) 15%, transparent);
}

.qw-submit {
  flex-shrink: 0;
  padding: 10px 18px;
  height: 44px;
  font-family: var(--qw-font);
  font-size: 14px;
  font-weight: 600;
  background: var(--qw-accent);
  color: #fff;
  border: none;
  border-radius: var(--qw-radius-sm);
  cursor: pointer;
  transition: opacity .15s, transform .1s;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 6px;
}

.qw-submit:hover:not(:disabled) { opacity: .88; }
.qw-submit:active:not(:disabled) { transform: scale(.97); }
.qw-submit:disabled { opacity: .45; cursor: not-allowed; }

/* ---- Konversationsflöde ---- */
.qw-konversation {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.qw-rad {
  padding: var(--qw-gap) var(--qw-gap);
  border-bottom: 1px solid var(--qw-border);
}

.qw-rad:last-child {
  border-bottom: none;
}

/* Frågans rad */
.qw-rad--fraga {
  background: var(--qw-surface);
}

.qw-fraga-text {
  font-weight: 600;
  color: var(--qw-text);
}

/* Svarets rad */
.qw-rad--svar {
  background: var(--qw-bg);
}

/* ---- Stycken med fotnoter ---- */
.qw-stycken {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: var(--qw-gap-sm);
}

.qw-stycke {
  line-height: 1.7;
}

/* Fotnot-knappar i löptext */
.qw-fn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-size: 10px;
  font-weight: 700;
  font-family: var(--qw-mono);
  background: var(--qw-fn-bg);
  color: var(--qw-accent);
  border: 1px solid var(--qw-border);
  border-radius: 4px;
  cursor: pointer;
  vertical-align: super;
  text-decoration: none;
  transition: background .12s, color .12s, transform .1s;
  line-height: 1;
  margin-left: 1px;
}

.qw-fn:hover {
  background: var(--qw-accent-bg);
  color: var(--qw-accent);
  transform: translateY(-1px);
}

.qw-fn.qw-fn--aktiv {
  background: var(--qw-accent);
  color: #fff;
  border-color: var(--qw-accent);
}

/* ---- Loader / cursor ---- */
.qw-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--qw-accent);
  border-radius: 1px;
  vertical-align: text-bottom;
  animation: qw-blink 1s step-end infinite;
  margin-left: 1px;
}

@keyframes qw-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0; }
}

/* ---- Förbehåll (avskilt, aldrig bland stycken) ---- */
.qw-forbehall {
  margin-top: var(--qw-gap-sm);
  padding: 10px 14px;
  background: var(--qw-note-bg);
  border: 1px solid var(--qw-note-border);
  border-radius: var(--qw-radius-sm);
  font-size: 13px;
  color: var(--qw-note-text);
  line-height: 1.55;
}

.qw-forbehall::before {
  content: "Not: ";
  font-weight: 600;
}

/* ---- Kan inte besvaras / fel ---- */
.qw-inget-svar {
  font-style: italic;
  color: var(--qw-text-muted);
  padding: 8px 0;
}

.qw-fel {
  padding: 10px 14px;
  background: var(--qw-error-bg);
  color: var(--qw-error-text);
  border-radius: var(--qw-radius-sm);
  font-size: 13px;
}

/* ---- Attribution (CC-BY) ---- */
.qw-attribution {
  margin-top: var(--qw-gap-sm);
  font-size: 12px;
  color: var(--qw-text-muted);
  line-height: 1.5;
}

.qw-attribution ul {
  list-style: none;
  padding-left: 0;
}

.qw-attribution li + li {
  margin-top: 2px;
}

/* ---- Källpanel ---- */
.qw-kallpanel-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: var(--qw-gap-sm);
  padding: 6px 12px;
  font-family: var(--qw-font);
  font-size: 12px;
  font-weight: 600;
  color: var(--qw-text-muted);
  background: var(--qw-fn-bg);
  border: 1px solid var(--qw-border);
  border-radius: 20px;
  cursor: pointer;
  transition: background .12s, color .12s;
}

.qw-kallpanel-toggle:hover {
  background: var(--qw-accent-bg);
  color: var(--qw-accent);
  border-color: color-mix(in srgb, var(--qw-accent) 30%, transparent);
}

.qw-kallpanel-toggle-ikon {
  font-style: normal;
  transition: transform .2s;
}

.qw-kallpanel-toggle[aria-expanded="true"] .qw-kallpanel-toggle-ikon {
  transform: rotate(180deg);
}

.qw-kallpanel {
  margin-top: var(--qw-gap-sm);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.qw-kallpanel[hidden] {
  display: none;
}

/* Källkort */
.qw-kallkort {
  background: var(--qw-surface);
  border: 1px solid var(--qw-border);
  border-radius: var(--qw-radius-sm);
  overflow: hidden;
  transition: box-shadow .15s;
}

.qw-kallkort:target,
.qw-kallkort.qw-kallkort--markerad {
  outline: 2px solid var(--qw-accent);
  outline-offset: 1px;
  box-shadow: var(--qw-shadow);
}

.qw-kallkort--harledd {
  background: var(--qw-derived-bg);
  border-color: var(--qw-derived-border);
}

.qw-kallkort-huvud {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 14px;
  flex-wrap: wrap;
}

.qw-kallkort-id {
  font-family: var(--qw-mono);
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: var(--qw-accent);
  border-radius: 4px;
  padding: 1px 6px;
  flex-shrink: 0;
}

.qw-kallkort--harledd .qw-kallkort-id {
  background: var(--qw-derived-text);
}

.qw-kallkort-etikett {
  font-weight: 600;
  font-size: 14px;
  flex: 1 1 0;
  min-width: 0;
  word-break: break-word;
}

.qw-kallkort-harledd-badge {
  font-size: 11px;
  color: var(--qw-derived-text);
  background: var(--qw-derived-bg);
  border: 1px solid var(--qw-derived-border);
  border-radius: 4px;
  padding: 1px 6px;
  flex-shrink: 0;
}

.qw-kallkort-kropp {
  padding: 0 14px 12px;
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 3px 14px;
  font-size: 13px;
}

.qw-kallkort-nyck {
  color: var(--qw-text-muted);
  white-space: nowrap;
  padding-top: 1px;
}

.qw-kallkort-vard {
  color: var(--qw-text);
  word-break: break-word;
  min-width: 0;
}

.qw-kallkort-lankar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 14px;
  border-top: 1px solid var(--qw-border);
  background: var(--qw-fn-bg);
}

.qw-lank {
  font-size: 12px;
  color: var(--qw-accent);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 4px;
  padding: 2px 4px;
  transition: background .12s;
  word-break: break-all;
}

.qw-lank:hover {
  background: var(--qw-accent-bg);
  text-decoration: underline;
}

/* Härledda poster har ingen URL att peka på — maskinfältet bär formeln som
   text. Den ska inte se ut som, eller bete sig som, en länk. */
.qw-lank--text {
  cursor: default;
  opacity: .85;
}

.qw-lank--text:hover {
  background: none;
  text-decoration: none;
}

.qw-lank-maskin {
  color: var(--qw-text-muted);
  font-family: var(--qw-mono);
  font-size: 11px;
}

/* ---- Dimensioner (pill-lista) ---- */
.qw-dim-lista {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  list-style: none;
}

.qw-dim-pill {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--qw-fn-bg);
  border: 1px solid var(--qw-border);
  border-radius: 20px;
  color: var(--qw-text-muted);
}

/* ---- Spinner ---- */
.qw-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: qw-spin .7s linear infinite;
}

@keyframes qw-spin {
  to { transform: rotate(360deg); }
}

/* ---- Tom-state ---- */
.qw-tom {
  padding: var(--qw-gap);
  text-align: center;
  color: var(--qw-text-muted);
  font-size: 14px;
  line-height: 1.6;
}

.qw-tom-ikon {
  font-size: 32px;
  margin-bottom: 8px;
  display: block;
}

/* ---- Responsivitet ---- */
@media (max-width: 420px) {
  .qw-form {
    flex-direction: column;
    align-items: stretch;
  }
  .qw-submit {
    width: 100%;
    justify-content: center;
    height: 44px;
  }
  .qw-kallkort-kropp {
    grid-template-columns: 1fr;
    gap: 2px;
  }
  .qw-kallkort-nyck {
    font-weight: 600;
    font-size: 11px;
    letter-spacing: .04em;
    text-transform: uppercase;
    margin-top: 6px;
  }
  .qw-kallkort-lankar {
    flex-direction: column;
    gap: 6px;
  }
}
`;

  // -------------------------------------------------------------------------
  // Hjälpfunktioner
  // -------------------------------------------------------------------------

  /** Skapar ett DOM-element med valfria attribut och barn. */
  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") e.className = v;
        else if (k === "textContent") e.textContent = v;
        else if (k === "innerHTML") e.innerHTML = v;
        else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
        else e.setAttribute(k, v);
      }
    }
    for (const c of children) {
      if (c == null) continue;
      e.append(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  /** Formaterar ett ISO-datum till läsbar form. */
  function formateraDatum(iso) {
    if (!iso) return null;
    try {
      return new Date(iso).toLocaleString("sv-SE", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      }).replace(",", "");
    } catch {
      return iso;
    }
  }

  /**
   * Sätter in fotnot-knappar i en textsträng.
   * Returnerar ett DocumentFragment.
   *
   * Källhänvisningar från modellen är F-id:n: ["F1","F2"]. Vi mappar dem
   * till sekvensnummer per svar (1, 2, 3…) så löptexten visar [1] [2].
   */
  function renderaTextMedFotnoter(text, kallIds, fotnummerFranId, onKlick) {
    // Bygger en fragment med löptexten och insprängda fotnotknappar
    const frag = document.createDocumentFragment();

    // Hämta unika fotnotnummer i ordning de nämns
    const nummerdIds = kallIds
      .map(id => ({ id, nr: fotnummerFranId(id) }))
      .filter(x => x.nr != null);

    // Lägg texten som nod
    frag.appendChild(document.createTextNode(text));

    // Lägg fotnoterna som inline-knappar efter texten
    for (const { id, nr } of nummerdIds) {
      const btn = el("button", {
        className: "qw-fn",
        title: `Källa ${id}`,
        "aria-label": `Visa källa ${id}`,
        "data-kallid": id,
        onclick: (e) => { e.preventDefault(); onKlick(id, btn); },
      }, String(nr));
      frag.appendChild(btn);
    }

    return frag;
  }

  /** Bygger ett källkort (en Faktapost). */
  function byggKallkort(post, nr) {
    const ar_harledd = post.harledd === true;
    const klass = ar_harledd ? "qw-kallkort qw-kallkort--harledd" : "qw-kallkort";
    const kort = el("div", { className: klass, id: `qw-kall-${post.id}` });

    // Huvud
    const huvud = el("div", { className: "qw-kallkort-huvud" });
    huvud.appendChild(el("span", { className: "qw-kallkort-id" }, String(nr)));
    huvud.appendChild(el("span", { className: "qw-kallkort-etikett" }, post.etikett || post.id));
    if (ar_harledd) {
      huvud.appendChild(el("span", { className: "qw-kallkort-harledd-badge" }, "Beräknat"));
    }
    kort.appendChild(huvud);

    // Kropp — metadata
    const kropp = el("div", { className: "qw-kallkort-kropp" });

    function rad(nyck, vard) {
      if (!vard) return;
      kropp.appendChild(el("span", { className: "qw-kallkort-nyck" }, nyck));
      kropp.appendChild(el("span", { className: "qw-kallkort-vard" }, vard));
    }

    rad("Myndighet", post.myndighet);
    rad("Dataset", post.dataset);
    rad("Period", post.period);

    if (post.hamtad) {
      rad("Hämtad", formateraDatum(post.hamtad));
    }

    if (ar_harledd && post.harledd_av && post.harledd_av.length > 0) {
      rad("Beräknad ur", post.harledd_av.join(", "));
    }

    // Dimensioner — pill-lista
    if (post.dimensioner && Object.keys(post.dimensioner).length > 0) {
      kropp.appendChild(el("span", { className: "qw-kallkort-nyck" }, "Urval"));
      const lista = el("ul", { className: "qw-dim-lista" });
      for (const [k, v] of Object.entries(post.dimensioner)) {
        lista.appendChild(el("li", { className: "qw-dim-pill" }, `${k}: ${v}`));
      }
      kropp.appendChild(lista);
    }

    rad("Licens", post.licens);

    kort.appendChild(kropp);

    // Länkar
    const lankar = el("div", { className: "qw-kallkort-lankar" });

    if (post.lank_manniska) {
      lankar.appendChild(
        el("a", {
          className: "qw-lank",
          href: post.lank_manniska,
          target: "_blank",
          rel: "noopener noreferrer",
          title: "Öppna myndighetens sida",
        }, "↗ Myndighetens sida")
      );
    }

    if (post.lank_maskin) {
      // En härledd post har inget API-anrop — lank_maskin bär formeln som text
      // ("beräkning: (F1 − F2) / F2"). Den får inte renderas som en <a href>:
      // webbläsaren tolkar den då som en relativ URL och användaren får en
      // klickbar länk som leder ingenstans.
      const arLank = /^https?:\/\//i.test(post.lank_maskin);
      lankar.appendChild(
        arLank
          ? el("a", {
              className: "qw-lank qw-lank-maskin",
              href: post.lank_maskin,
              target: "_blank",
              rel: "noopener noreferrer",
              title: post.lank_maskin,
            }, "API-anrop")
          : el("span", {
              className: "qw-lank qw-lank-maskin qw-lank--text",
              title: post.lank_maskin,
            }, post.lank_maskin)
      );
    }

    if (lankar.childElementCount > 0) {
      kort.appendChild(lankar);
    }

    return kort;
  }

  // -------------------------------------------------------------------------
  // Widget-klass
  // -------------------------------------------------------------------------

  class QuietWidget {
    constructor(container, apiUrl) {
      this._container = container;
      this._apiUrl = apiUrl.replace(/\/$/, "");
      this._aktivtAnrop = null; // AbortController för pågående SSE

      this._inject_css();
      this._bygg_ui();
    }

    _inject_css() {
      if (document.getElementById("quiet-widget-css")) return;
      const s = document.createElement("style");
      s.id = "quiet-widget-css";
      s.textContent = CSS;
      document.head.appendChild(s);
    }

    _bygg_ui() {
      const c = this._container;
      c.setAttribute("role", "main");
      c.setAttribute("aria-label", "Quiet Öppen Data — chattgränssnitt");

      // Formulär
      this._form = el("form", {
        className: "qw-form",
        onsubmit: (e) => { e.preventDefault(); this._skicka(); },
      });

      const inputWrap = el("div", { className: "qw-input-wrap" });
      const label = el("label", {
        className: "qw-label",
        for: "qw-input",
      }, "Ställ en fråga om offentlig statistik");
      this._input = el("textarea", {
        className: "qw-input",
        id: "qw-input",
        placeholder: "T.ex. "Vad är Riksbankens referensränta?" …",
        rows: "1",
        "aria-label": "Din fråga",
        onkeydown: (e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            this._skicka();
          }
        },
        oninput: () => this._auto_hojd(),
      });
      inputWrap.appendChild(label);
      inputWrap.appendChild(this._input);

      this._knapp = el("button", {
        type: "submit",
        className: "qw-submit",
        id: "qw-submit",
        "aria-label": "Skicka frågan",
      }, "Fråga");

      this._form.appendChild(inputWrap);
      this._form.appendChild(this._knapp);

      // Konversation
      this._konvDiv = el("div", {
        className: "qw-konversation",
        "aria-live": "polite",
        "aria-label": "Konversation",
      });

      // Tom-state
      this._tomDiv = el("div", { className: "qw-tom" });
      this._tomDiv.appendChild(el("span", { className: "qw-tom-ikon" }, "🏛️"));
      this._tomDiv.appendChild(
        el("p", {}, "Ställ en fråga om offentlig statistik, myndighetsdata eller lagtext.")
      );
      this._tomDiv.appendChild(
        el("p", { style: "font-size:12px;margin-top:6px;" }, "Alla svar är belagda med källhänvisningar till myndighets-API:er.")
      );

      c.appendChild(this._form);
      c.appendChild(this._tomDiv);
      c.appendChild(this._konvDiv);
    }

    _auto_hojd() {
      const ta = this._input;
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
    }

    _lås_ui(las) {
      this._input.disabled = las;
      this._knapp.disabled = las;
      if (las) {
        this._knapp.innerHTML = "";
        this._knapp.appendChild(el("span", { className: "qw-spinner" }));
        this._knapp.appendChild(document.createTextNode(" Hämtar…"));
      } else {
        this._knapp.textContent = "Fråga";
      }
    }

    async _skicka() {
      const fraga = this._input.value.trim();
      if (!fraga) return;

      // Avbryt eventuellt pågående anrop
      if (this._aktivtAnrop) {
        this._aktivtAnrop.abort();
        this._aktivtAnrop = null;
      }

      // Töm input och dölj tom-state
      this._input.value = "";
      this._auto_hojd();
      this._tomDiv.style.display = "none";
      this._lås_ui(true);

      // Lägg till fråge-rad
      const fragaRad = el("div", { className: "qw-rad qw-rad--fraga" });
      fragaRad.appendChild(el("p", { className: "qw-fraga-text" }, fraga));
      this._konvDiv.appendChild(fragaRad);

      // Skapa svar-rad med cursor
      const svarRad = el("div", { className: "qw-rad qw-rad--svar" });
      const styckenDiv = el("div", { className: "qw-stycken" });
      const cursorEl = el("span", { className: "qw-cursor", "aria-hidden": "true" });
      const aktivtStycke = el("p", { className: "qw-stycke" });
      aktivtStycke.appendChild(cursorEl);
      styckenDiv.appendChild(aktivtStycke);
      svarRad.appendChild(styckenDiv);
      this._konvDiv.appendChild(svarRad);
      svarRad.scrollIntoView({ behavior: "smooth", block: "nearest" });

      // --- SSE-state per svar ---
      const fotnummerFranId = new Map(); // id → sekvensnr
      let nastaNr = 1;

      const _registreraFotnummer = (ids) => {
        for (const id of ids) {
          if (!fotnummerFranId.has(id)) {
            fotnummerFranId.set(id, nastaNr++);
          }
        }
      };

      const _fotnrFn = (id) => fotnummerFranId.get(id);

      // Källpanel-behållare (byggs när "kallor"-händelsen anländer)
      let kallpanelDiv = null;
      let kallpanelToggle = null;
      let fnKnappar = []; // alla fotnot-knappar i denna rad, för markering

      const _markeraKall = (id, triggaKnapp) => {
        // Demarkera alla i denna rad
        for (const [, knappArr] of fnMappaKnappar) {
          for (const k of knappArr) k.classList.remove("qw-fn--aktiv");
        }
        if (triggaKnapp) triggaKnapp.classList.add("qw-fn--aktiv");

        if (!kallpanelDiv) return;

        // Öppna källpanelen om stängd
        if (kallpanelDiv.hidden) {
          kallpanelDiv.hidden = false;
          if (kallpanelToggle) kallpanelToggle.setAttribute("aria-expanded", "true");
        }

        const kall = kallpanelDiv.querySelector(`#qw-kall-${id}`);
        if (kall) {
          // Markera korten
          kallpanelDiv.querySelectorAll(".qw-kallkort").forEach(k => k.classList.remove("qw-kallkort--markerad"));
          kall.classList.add("qw-kallkort--markerad");
          kall.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      };

      // Map från källid → array av knappar (kan nämnas i flera stycken)
      const fnMappaKnappar = new Map();

      const _laggTillStycke = (text, kallIds) => {
        _registreraFotnummer(kallIds);

        // Ta bort cursor från "aktivt stycke" om det finns
        const gammalCursor = styckenDiv.querySelector(".qw-cursor");
        if (gammalCursor) gammalCursor.remove();

        const p = el("p", { className: "qw-stycke" });

        const frag = renderaTextMedFotnoter(
          text,
          kallIds,
          _fotnrFn,
          (id, knapp) => _markeraKall(id, knapp)
        );
        p.appendChild(frag);

        // Samla fnknappar per id
        p.querySelectorAll(".qw-fn").forEach(btn => {
          const id = btn.dataset.kallid;
          if (!fnMappaKnappar.has(id)) fnMappaKnappar.set(id, []);
          fnMappaKnappar.get(id).push(btn);
        });

        styckenDiv.appendChild(p);
        svarRad.scrollIntoView({ behavior: "smooth", block: "nearest" });
      };

      const _visaKallor = (kallor) => {
        // Ta bort cursor
        const gammalCursor = styckenDiv.querySelector(".qw-cursor");
        if (gammalCursor) gammalCursor.remove();

        if (!kallor || kallor.length === 0) return;

        // Knapp för att toggla källpanelen
        kallpanelToggle = el("button", {
          className: "qw-kallpanel-toggle",
          "aria-expanded": "false",
          "aria-controls": "qw-kp-" + Date.now(),
          onclick: () => {
            const nyttTillstand = kallpanelDiv.hidden;
            kallpanelDiv.hidden = !nyttTillstand;
            kallpanelToggle.setAttribute("aria-expanded", String(nyttTillstand));
          },
        });
        kallpanelToggle.appendChild(
          el("span", {}, `📋 ${kallor.length} källa${kallor.length !== 1 ? "r" : ""}`)
        );
        const togglIkon = el("em", { className: "qw-kallpanel-toggle-ikon", "aria-hidden": "true" }, "▾");
        kallpanelToggle.appendChild(togglIkon);

        svarRad.appendChild(kallpanelToggle);

        // Källpanelen
        kallpanelDiv = el("div", { className: "qw-kallpanel", hidden: true });
        kallpanelToggle.id = kallpanelToggle.getAttribute("aria-controls");

        for (const post of kallor) {
          const nr = fotnummerFranId.get(post.id) || nastaNr++;
          const kort = byggKallkort(post, nr);
          kallpanelDiv.appendChild(kort);
        }

        svarRad.appendChild(kallpanelDiv);
      };

      const _visaAttribution = (attributioner) => {
        if (!attributioner || attributioner.length === 0) return;
        const div = el("div", { className: "qw-attribution" });
        div.appendChild(el("span", { style: "font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--qw-text-muted);" }, "Källa (CC-BY): "));
        const ul = el("ul", {});
        for (const a of attributioner) {
          ul.appendChild(el("li", { textContent: a }));
        }
        div.appendChild(ul);
        svarRad.appendChild(div);
      };

      const _visaForbehall = (text) => {
        if (!text) return;
        svarRad.appendChild(el("div", { className: "qw-forbehall" }, text));
      };

      const _visaFel = (meddelande) => {
        const gammalCursor = styckenDiv.querySelector(".qw-cursor");
        if (gammalCursor) gammalCursor.remove();
        svarRad.appendChild(el("div", { className: "qw-fel" }, meddelande || "Ett tekniskt fel inträffade."));
      };

      const _visaIngetSvar = (forbehall) => {
        const gammalCursor = styckenDiv.querySelector(".qw-cursor");
        if (gammalCursor) gammalCursor.remove();
        svarRad.appendChild(
          el("p", { className: "qw-inget-svar" },
            forbehall || "Det hittade jag inte i källorna.")
        );
      };

      // --- SSE-anrop ---
      const controller = new AbortController();
      this._aktivtAnrop = controller;

      try {
        const resp = await fetch(`${this._apiUrl}/fraga`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fraga }),
          signal: controller.signal,
        });

        if (!resp.ok) {
          const felinf = await resp.json().catch(() => ({}));
          _visaFel(felinf.detail || `Serverfel (${resp.status}).`);
          this._lås_ui(false);
          this._aktivtAnrop = null;
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let aktuelltHandelseTyp = "message";

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // SSE-parsning: blankrad avslutar ett event
          const block_re = /([^\n]*)\n/g;
          let handelse_rader = [];
          let m;

          while ((m = block_re.exec(buffer)) !== null) {
            const rad = m[1];
            if (rad === "") {
              // Slut på ett SSE-meddelande — behandla insamlade rader
              this._behandlaSSEHandelse(
                aktuelltHandelseTyp,
                handelse_rader,
                { _laggTillStycke, _visaKallor, _visaAttribution, _visaForbehall, _visaFel, _visaIngetSvar }
              );
              handelse_rader = [];
              aktuelltHandelseTyp = "message";
            } else if (rad.startsWith("event: ")) {
              aktuelltHandelseTyp = rad.slice(7).trim();
            } else if (rad.startsWith("data: ")) {
              handelse_rader.push(rad.slice(6));
            }
          }

          // Behåll bara den del av bufferten som inte behandlats
          const senasteNL = buffer.lastIndexOf("\n");
          buffer = senasteNL >= 0 ? buffer.slice(senasteNL + 1) : buffer;
        }

      } catch (err) {
        if (err.name !== "AbortError") {
          _visaFel("Anslutningen avbröts. Kontrollera din uppkoppling och försök igen.");
        } else {
          // Manuellt avbrott — rensa rad
          svarRad.remove();
        }
      } finally {
        // Ta bort cursor om den finns kvar
        const gc = styckenDiv.querySelector(".qw-cursor");
        if (gc) gc.remove();

        this._lås_ui(false);
        this._aktivtAnrop = null;
        this._input.focus();
      }
    }

    /** Tolkar ett färdigt SSE-event och kallar rätt renderfunktion. */
    _behandlaSSEHandelse(typ, rader, fns) {
      if (rader.length === 0) return;
      let data;
      try {
        data = JSON.parse(rader.join(""));
      } catch {
        return;
      }

      const { _laggTillStycke, _visaKallor, _visaAttribution, _visaForbehall, _visaFel, _visaIngetSvar } = fns;

      switch (typ) {
        case "stycke":
          // Arkitekturkrav: rendera inte ett stycke utan källhänvisningar
          if (!data.kallor || data.kallor.length === 0) break;
          _laggTillStycke(data.text || "", data.kallor);
          break;

        case "kallor":
          _visaKallor(data.kallor);
          break;

        case "attribution":
          _visaAttribution(data.attribution);
          break;

        case "forbehall":
          // Förbehåll renderas avskilt som not, ALDRIG bland stycken
          _visaForbehall(data.forbehall);
          break;

        case "svar":
          // kan_besvaras: false
          if (!data.kan_besvaras) {
            _visaIngetSvar(data.forbehall);
          }
          break;

        case "fel":
          _visaFel(data.meddelande);
          break;

        case "klart":
          // Ingenting att göra — finally-blocket tar hand om cursor och unlock
          break;

        default:
          break;
      }
    }
  }

  // -------------------------------------------------------------------------
  // Bootstrap — leta reda på container(s) och starta
  // -------------------------------------------------------------------------

  function init() {
    const containers = document.querySelectorAll("[data-quiet-widget], #quiet-widget");
    if (containers.length === 0) {
      // Fallback: om scriptet laddas utan en dedikerad container, skapa en
      console.warn("[quiet-widget] Ingen container hittades. Lägg till ett element med id='quiet-widget' eller data-quiet-widget-attribut.");
      return;
    }

    for (const c of containers) {
      // Läs API-URL från attributet, faller tillbaka på relativ sökväg
      const apiUrl = c.dataset.api || c.getAttribute("data-api") || "";
      new QuietWidget(c, apiUrl);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();
