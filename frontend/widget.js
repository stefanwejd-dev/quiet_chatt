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
#quiet-widget {
  /* Ärver quiet.nu:s egna design-tokens (tokens.css) när widgeten körs på
     sajten — inklusive ljust/mörkt tema, som redan hanteras där. Reservvärden
     efter kommatecknet gäller i fristående bruk (t.ex. test.html) och speglar
     samma paletts ljusa läge. Se motsvarande mörka reservvärden nedan. */
  --qw-font:        var(--font-brod, ui-sans-serif, system-ui, sans-serif);
  --qw-font-rubrik: var(--font-rubrik, var(--qw-font));
  --qw-mono: ui-monospace, "SF Mono", "Fira Code", monospace;

  --qw-radius:    20px;
  --qw-radius-sm: 12px;
  --qw-gap:       20px;
  --qw-gap-sm:    12px;

  --qw-bg:         var(--cream, #fcf4eb);
  --qw-surface:    var(--paper, #fffbf7);
  --qw-border:     var(--line, #dfd1c7);
  --qw-shadow:     0 1px 2px rgba(41,28,21,.04), 0 10px 28px rgba(41,28,21,.07);
  --qw-text:       var(--ink, #291c15);
  --qw-text-muted: var(--muted, #72665e);
  --qw-accent:     var(--terra-dark, #913814);
  --qw-accent-soft:var(--terra, #b95c3a);
  --qw-on-accent:  var(--cream, #fffaf5);
  --qw-accent-bg:  color-mix(in srgb, var(--qw-accent-soft) 12%, var(--qw-surface));
  --qw-fn-bg:      color-mix(in srgb, var(--qw-accent-soft) 10%, var(--qw-surface));
  --qw-input-bg:   var(--qw-surface);
  --qw-note-bg:    color-mix(in srgb, #c9962b 14%, var(--qw-surface));
  --qw-note-border:color-mix(in srgb, #c9962b 40%, var(--qw-border));
  --qw-note-text:  #8a6417;
  --qw-error-bg:   color-mix(in srgb, #b3402c 10%, var(--qw-surface));
  --qw-error-border: color-mix(in srgb, #b3402c 35%, var(--qw-border));
  --qw-error-text: #8a2f1f;
  --qw-derived-border: color-mix(in srgb, var(--qw-accent-soft) 55%, var(--qw-border));
}

@media (prefers-color-scheme: dark) {
  #quiet-widget {
    --qw-bg:         var(--cream, #1b120d);
    --qw-surface:    var(--paper, #241811);
    --qw-border:     var(--line, #3d2e25);
    --qw-shadow:     0 1px 2px rgba(0,0,0,.3), 0 10px 28px rgba(0,0,0,.25);
    --qw-text:       var(--ink, #fcf4eb);
    --qw-text-muted: var(--muted, #b3a49a);
    --qw-accent:     var(--terra-dark, #d97a52);
    --qw-on-accent:  #1b120d;
    --qw-note-text:  #d9b871;
    --qw-error-text: #e08d7a;
  }
}

#quiet-widget * {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

#quiet-widget {
  display: flex;
  flex-direction: column;
  gap: var(--qw-gap-sm);
  font-family: var(--qw-font);
  font-size: 15px;
  line-height: 1.6;
  color: var(--qw-text);
  max-width: 760px;
  width: 100%;
  /* Fast fönsterhöjd, som i Claude/ChatGPT/Gemini: samtalet rullar för sig
     inuti .qw-scroll, formuläret ligger stilla längst ner i den här boxen
     (inte i webbläsarfönstret — widgeten sitter mitt i en sida med egen
     header/sidopanel/footer omkring sig). */
  height: min(72vh, 640px);
  min-height: 360px;
}

/* Rullbart område: tom-state + konversation. Allt utom formuläret. */
.qw-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 4px;
  display: flex;
  flex-direction: column;
  gap: var(--qw-gap);
}

/* ---- Formulär: rundad "pill", i stil med moderna AI-chattar ---- */
.qw-form {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  flex-shrink: 0;
  padding: 10px 10px 10px 22px;
  background: var(--qw-surface);
  border: 1px solid var(--qw-border);
  border-radius: 26px;
  box-shadow: var(--qw-shadow);
  transition: border-color .15s, box-shadow .15s;
}

.qw-form:focus-within {
  border-color: var(--qw-accent-soft);
}

/* Synligt för skärmläsare, osynligt visuellt — placeholdern bär den visuella
   ledtråden, precis som i Claude/ChatGPT/Gemini. */
.qw-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.qw-input-wrap {
  flex: 1 1 0;
  min-width: 0;
}

.qw-input {
  display: block;
  width: 100%;
  padding: 12px 8px;
  font-family: var(--qw-font);
  font-size: 15px;
  line-height: 1.5;
  background: transparent;
  border: none;
  color: var(--qw-text);
  resize: none;
  min-height: 26px;
  max-height: 140px;
  outline: none;
  overflow-y: auto;
}

.qw-input::placeholder {
  color: var(--qw-text-muted);
}

.qw-submit {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  padding: 0;
  background: var(--qw-accent-soft);
  color: var(--qw-on-accent);
  border: none;
  border-radius: 50%;
  cursor: pointer;
  transition: background .15s, transform .1s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.qw-submit svg { width: 18px; height: 18px; }

.qw-submit:hover:not(:disabled) { background: var(--qw-accent); }
.qw-submit:active:not(:disabled) { transform: scale(.93); }
.qw-submit:disabled { opacity: .4; cursor: not-allowed; }

/* ---- Konversationsflöde — inga tabellrader, ett tyst samtalsflöde ---- */
.qw-konversation {
  display: flex;
  flex-direction: column;
  gap: var(--qw-gap);
}

.qw-rad {
  display: flex;
}

/* Frågan: högerjusterad bubbla, som i Claude/ChatGPT/Gemini */
.qw-rad--fraga {
  justify-content: flex-end;
}

.qw-fraga-text {
  max-width: 85%;
  padding: 13px 18px;
  background: var(--qw-accent-soft);
  color: var(--qw-on-accent);
  border-radius: 18px;
  border-bottom-right-radius: 4px;
  line-height: 1.5;
}

/* Svaret: fri löptext utan bubbla/ram, som ett AI-svar */
.qw-rad--svar {
  display: block;
  padding-top: 2px;
}

/* ---- Stycken med fotnoter ---- */
.qw-stycken {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: var(--qw-gap-sm);
  max-width: 66ch;
}

.qw-stycke {
  line-height: 1.7;
}

/* Fotnot-knappar i löptext */
.qw-fn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 17px;
  height: 17px;
  padding: 0 3px;
  font-size: 10px;
  font-weight: 600;
  font-family: var(--qw-font);
  background: var(--qw-fn-bg);
  color: var(--qw-accent);
  border: 1px solid var(--qw-border);
  border-radius: 999px;
  cursor: pointer;
  vertical-align: super;
  text-decoration: none;
  transition: background .12s, color .12s, transform .1s;
  line-height: 1;
  margin-left: 2px;
}

.qw-fn:hover {
  background: var(--qw-accent-bg);
  color: var(--qw-accent);
  transform: translateY(-1px);
}

.qw-fn.qw-fn--aktiv {
  background: var(--qw-accent-soft);
  color: var(--qw-on-accent);
  border-color: var(--qw-accent-soft);
}

/* ---- Loader / cursor ---- */
.qw-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--qw-accent-soft);
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
  max-width: 66ch;
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
  max-width: 66ch;
  padding: 10px 14px;
  background: var(--qw-error-bg);
  border: 1px solid var(--qw-error-border);
  color: var(--qw-error-text);
  border-radius: var(--qw-radius-sm);
  font-size: 13px;
}

/* ---- Attribution (CC-BY) ---- */
.qw-attribution {
  margin-top: var(--qw-gap-sm);
  max-width: 66ch;
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
  padding: 6px 14px 6px 10px;
  font-family: var(--qw-font);
  font-size: 12px;
  font-weight: 600;
  color: var(--qw-text-muted);
  background: var(--qw-fn-bg);
  border: 1px solid var(--qw-border);
  border-radius: 999px;
  cursor: pointer;
  transition: background .12s, color .12s, border-color .12s;
}

.qw-kallpanel-toggle svg {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.qw-kallpanel-toggle:hover {
  background: var(--qw-accent-bg);
  color: var(--qw-accent);
  border-color: color-mix(in srgb, var(--qw-accent-soft) 40%, transparent);
}

.qw-kallpanel-toggle-ikon {
  display: inline-flex;
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
  max-width: 66ch;
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
  outline: 2px solid var(--qw-accent-soft);
  outline-offset: 1px;
  box-shadow: var(--qw-shadow);
}

.qw-kallkort--harledd {
  border-style: dashed;
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
  color: var(--qw-on-accent);
  background: var(--qw-accent-soft);
  border-radius: 999px;
  padding: 1px 7px;
  flex-shrink: 0;
}

.qw-kallkort--harledd .qw-kallkort-id {
  background: var(--qw-accent);
}

.qw-kallkort-etikett {
  font-family: var(--qw-font-rubrik);
  font-weight: 500;
  font-size: 14px;
  flex: 1 1 0;
  min-width: 0;
  word-break: break-word;
}

.qw-kallkort-harledd-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--qw-accent);
  background: var(--qw-accent-bg);
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
  border-radius: 999px;
  color: var(--qw-text-muted);
}

/* ---- Spinner ---- */
.qw-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid color-mix(in srgb, var(--qw-on-accent) 40%, transparent);
  border-top-color: var(--qw-on-accent);
  border-radius: 50%;
  animation: qw-spin .7s linear infinite;
}

@keyframes qw-spin {
  to { transform: rotate(360deg); }
}

/* ---- Tom-state ---- */
.qw-tom {
  padding: 4px var(--qw-gap-sm);
  color: var(--qw-text-muted);
  font-size: 14px;
  line-height: 1.6;
}

.qw-tom-ikon {
  color: var(--qw-accent-soft);
  margin-bottom: 10px;
  display: block;
}

.qw-tom-ikon svg { width: 26px; height: 26px; }

/* ---- Responsivitet ---- */
@media (max-width: 420px) {
  .qw-fraga-text {
    max-width: 92%;
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

  /** Pil-upp-ikonen på skicka-knappen, som SVG-markup (inline, ingen ikonfont). */
  const PIL_UPP_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg>';

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
        placeholder: "T.ex. ”Vad är Riksbankens referensränta?” …",
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
        title: "Skicka frågan",
      });
      this._knapp.innerHTML = PIL_UPP_SVG;

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
      const tomIkon = el("span", { className: "qw-tom-ikon", "aria-hidden": "true" });
      tomIkon.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
        'stroke-linecap="round" stroke-linejoin="round"><path d="M4 21h16"/>' +
        '<path d="M4 10h16"/><path d="M6 10V21"/><path d="M18 10V21"/>' +
        '<path d="M10 10V21"/><path d="M14 10V21"/><path d="M3 10l9-6 9 6"/></svg>';
      this._tomDiv.appendChild(tomIkon);
      this._tomDiv.appendChild(
        el("p", {}, "Ställ en fråga om offentlig statistik, myndighetsdata eller lagtext.")
      );
      this._tomDiv.appendChild(
        el("p", { style: "font-size:12px;margin-top:6px;" }, "Alla svar är belagda med källhänvisningar till myndighets-API:er.")
      );

      // Rullbart område: allt utom formuläret. Formuläret ligger kvar längst
      // ner (som i Claude/ChatGPT/Gemini) medan samtalet växer och rullar
      // uppåt inuti sin egen box — se #quiet-widget/.qw-scroll i CSS.
      this._scrollDiv = el("div", { className: "qw-scroll" });
      this._scrollDiv.appendChild(this._tomDiv);
      this._scrollDiv.appendChild(this._konvDiv);

      c.appendChild(this._scrollDiv);
      c.appendChild(this._form);
    }

    _auto_hojd() {
      const ta = this._input;
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
    }

    _lås_ui(las) {
      this._input.disabled = las;
      this._knapp.disabled = las;
      this._knapp.title = las ? "Hämtar svar…" : "Skicka frågan";
      this._knapp.setAttribute("aria-label", las ? "Hämtar svar…" : "Skicka frågan");
      if (las) {
        this._knapp.innerHTML = "";
        this._knapp.appendChild(el("span", { className: "qw-spinner" }));
      } else {
        this._knapp.innerHTML = PIL_UPP_SVG;
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
        const kallIkon = el("span", { "aria-hidden": "true" });
        kallIkon.innerHTML =
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round"><path d="M9 12h6"/>' +
          '<path d="M9 16h6"/><path d="M9 8h1"/>' +
          '<path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/></svg>';
        kallpanelToggle.appendChild(kallIkon);
        kallpanelToggle.appendChild(
          el("span", {}, `${kallor.length} källa${kallor.length !== 1 ? "r" : ""}`)
        );
        const togglIkon = el("span", { className: "qw-kallpanel-toggle-ikon", "aria-hidden": "true" });
        togglIkon.innerHTML =
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
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
