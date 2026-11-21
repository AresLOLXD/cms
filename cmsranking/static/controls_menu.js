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

/* Controls menu: the corner icon is a <details class="controls_menu"> whose
 * <summary> is the icon. The browser opens and closes it on a click of the
 * summary; this script adds the two ways out a <details> lacks: Escape (the
 * focus goes back to the summary) and a click anywhere outside the menu.
 *
 * It acts only on details.controls_menu[open], never on other <details>.
 * Load it with defer or at the end of the body.
 *
 * cmsranking/static/controls_menu.js MUST be an identical copy of this file
 * (a unit test checks it).
 */

(function () {
    "use strict";

    var OPEN_MENUS = "details.controls_menu[open]";

    document.addEventListener("DOMContentLoaded", function () {
        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape") {
                return;
            }
            var menus = document.querySelectorAll(OPEN_MENUS);
            for (var i = 0; i < menus.length; i++) {
                menus[i].open = false;
                menus[i].querySelector("summary").focus();
            }
        });

        // A click on the summary is inside the menu, so it is left to the
        // browser, which toggles the menu itself.
        document.addEventListener("click", function (event) {
            var menus = document.querySelectorAll(OPEN_MENUS);
            for (var i = 0; i < menus.length; i++) {
                if (!menus[i].contains(event.target)) {
                    menus[i].open = false;
                }
            }
        });
    });
})();
