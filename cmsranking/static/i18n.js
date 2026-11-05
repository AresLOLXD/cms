/* Contest Management System
 * Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

/* Spanish/English language of the ranking.
 *
 * The language is the saved choice (localStorage "cms.lang", "es" or "en");
 * without one it is Spanish when the first preferred language of the browser
 * starts with "es" and English in every other case. It is shown as lang and
 * data-lang on <html>, set right away: load this script synchronously in
 * <head>, after theme.js and before the other scripts.
 *
 * I18n.t(key) returns the text of the current language (else the English
 * one, else the key). At DOMContentLoaded the elements marked with
 * data-i18n="key" get that text, and data-i18n-attr="alt:key,title:key" sets
 * those attributes. The page may have a <select id="language"> with the
 * options es and en, which this script wires: a change is saved and the page
 * is reloaded once, so that everything drawn by the other scripts is drawn
 * again in the new language.
 *
 * The notice pages built by cmsranking/visibility.py cannot load this
 * script, so they inline a snippet that MUST pick the language the same way
 * (a unit test runs both).
 *
 * English is the source text and the one in Ranking.html (the fallback
 * without scripts). Entries with markup (the connection messages) are static
 * trusted text of this file: callers insert them with .html() and never
 * interpolate a value into them.
 */

(function () {
    "use strict";

    var KEY = "cms.lang";
    var root = document.documentElement;

    var catalog = {
        en: {
            "title.ranking": "Ranking",
            "title.user_prefix": "Ranking - ",
            "label.status": "Status",
            "time.elapsed": "Elapsed time",
            "time.remaining": "Remaining time",
            "time.current": "Current time",
            "powered.by": "Powered by",
            "search.placeholder": "Search Team",
            "alt.logo": "Logo",
            "alt.close": "Close",
            "alt.face": "Face",
            "alt.flag": "Flag",
            "button.show_tasks": "Show tasks",
            "button.totals_only": "Totals only",
            "button.show": "Show",
            "col.rank": "Rank",
            "col.name": "Name",
            "col.username": "Username",
            "col.team": "Team",
            "col.score": "Score",
            "col.time": "Time",
            "col.token": "Token",
            "label.global": "Global",
            "user.no_submissions": "no submissions",
            "user.yes": "Yes",
            "user.no": "No",
            "conn.reconnecting": "You are disconnected from the server but your browser is trying to connect.",
            "conn.connected": "You are connected to the server and are receiving live updates.",
            "conn.disconnected": "You are disconnected from the server but you can <a onclick=\"DataStore.create_event_source();\">try to connect</a>.",
            "conn.outdated": "Your local data cannot be updated. Please <a onclick=\"window.location.reload();\">reload the page</a>.",
            "conn.init_error": "An error occurred while loading the data. Check your connection and <a onclick=\"window.location.reload();\">reload the page</a>."
        },
        es: {
            "title.ranking": "Ranking",
            "title.user_prefix": "Ranking - ",
            "label.status": "Estado",
            "time.elapsed": "Tiempo transcurrido",
            "time.remaining": "Tiempo restante",
            "time.current": "Hora actual",
            "powered.by": "Con tecnología de",
            "search.placeholder": "Buscar equipo",
            "alt.logo": "Logo",
            "alt.close": "Cerrar",
            "alt.face": "Rostro",
            "alt.flag": "Bandera",
            "button.show_tasks": "Mostrar problemas",
            "button.totals_only": "Solo totales",
            "button.show": "Mostrar",
            "col.rank": "Lugar",
            "col.name": "Nombre",
            "col.username": "Usuario",
            "col.team": "Equipo",
            "col.score": "Puntaje",
            "col.time": "Tiempo",
            "col.token": "Token",
            "label.global": "Global",
            "user.no_submissions": "sin envíos",
            "user.yes": "Sí",
            "user.no": "No",
            "conn.reconnecting": "Estás desconectado del servidor, pero tu navegador está intentando conectarse.",
            "conn.connected": "Estás conectado al servidor y recibes actualizaciones en vivo.",
            "conn.disconnected": "Estás desconectado del servidor, pero puedes <a onclick=\"DataStore.create_event_source();\">intentar conectarte</a>.",
            "conn.outdated": "Tus datos locales no se pueden actualizar. Por favor <a onclick=\"window.location.reload();\">recarga la página</a>.",
            "conn.init_error": "Ocurrió un error al cargar los datos. Revisa tu conexión y <a onclick=\"window.location.reload();\">recarga la página</a>."
        }
    };

    // Every storage access may throw (blocked storage, private mode).
    function readChoice() {
        try {
            var value = window.localStorage.getItem(KEY);
            return value === "es" || value === "en" ? value : null;
        } catch (e) {
            return null;
        }
    }

    function writeChoice(choice) {
        try {
            window.localStorage.setItem(KEY, choice);
        } catch (e) {
            // The choice still applies to this page, just not to the next.
        }
    }

    // Only the first preferred language counts.
    function browserLanguage() {
        var nav = window.navigator;
        var first = nav.languages && nav.languages.length ?
            nav.languages[0] : nav.language;
        return typeof first === "string" &&
            first.toLowerCase().indexOf("es") === 0 ? "es" : "en";
    }

    var lang = readChoice() || browserLanguage();
    root.setAttribute("lang", lang);
    root.setAttribute("data-lang", lang);

    function t(key) {
        if (catalog[lang][key]) {
            return catalog[lang][key];
        }
        return catalog.en[key] || key;
    }

    window.I18n = { lang: lang, t: t, catalog: catalog };

    document.addEventListener("DOMContentLoaded", function () {
        var texts = document.querySelectorAll("[data-i18n]");
        for (var i = 0; i < texts.length; i++) {
            texts[i].textContent = t(texts[i].getAttribute("data-i18n"));
        }
        var attrs = document.querySelectorAll("[data-i18n-attr]");
        for (var j = 0; j < attrs.length; j++) {
            var pairs = attrs[j].getAttribute("data-i18n-attr").split(",");
            for (var k = 0; k < pairs.length; k++) {
                var pair = pairs[k].split(":");
                attrs[j].setAttribute(pair[0], t(pair[1]));
            }
        }

        var select = document.getElementById("language");
        if (!select) {
            return;
        }
        select.value = lang;
        select.addEventListener("change", function () {
            writeChoice(select.value);
            window.location.reload();
        });
    });
})();
