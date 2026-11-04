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

/* Light/dark theme: the choice (light, dark or system) is kept in
 * localStorage under "cms.theme" and shown as data-theme on <html>;
 * with "system" the attribute is absent and the CSS media query decides.
 *
 * Load it synchronously in <head>, so the attribute is set before the body
 * is painted. The page may have a <select id="theme"> with the options
 * system, light and dark, which this script wires.
 *
 * cmsranking/static/theme.js MUST be an identical copy of this file
 * (a unit test checks it).
 */

(function () {
    "use strict";

    var KEY = "cms.theme";
    var root = document.documentElement;

    // Every storage access may throw (blocked storage, private mode).
    function readChoice() {
        try {
            var value = window.localStorage.getItem(KEY);
            return value === "light" || value === "dark" ? value : "system";
        } catch (e) {
            return "system";
        }
    }

    function writeChoice(choice) {
        try {
            if (choice === "system") {
                window.localStorage.removeItem(KEY);
            } else {
                window.localStorage.setItem(KEY, choice);
            }
        } catch (e) {
            // The choice still applies to this page, just not to the next.
        }
    }

    function apply(choice) {
        if (choice === "system") {
            root.removeAttribute("data-theme");
        } else {
            root.setAttribute("data-theme", choice);
        }
    }

    apply(readChoice());

    document.addEventListener("DOMContentLoaded", function () {
        var select = document.getElementById("theme");
        if (!select) {
            return;
        }
        select.value = readChoice();
        select.addEventListener("change", function () {
            writeChoice(select.value);
            apply(select.value);
        });
    });
})();
