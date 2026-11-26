# Design

## Context

AWS navigation is the `ul.menu` at the top of the sidebar in `cms/server/admin/templates/base.html`, one list for the contest-less menu and one for the contest menu, both closed by the same `</ul>`. The corner controls menu is for personal controls only (spec `controls-menu`), so the manual link does not go there. AWS has guards that fail on any text outside gettext (`aws_i18n_guard_test.py`) and rendered-page tests that build `config` as a bare `SimpleNamespace(web_server=...)`: a template reading `config.admin_web_server` would break all of them. The manual exists in Spanish and English at `/es/latest/` and `/en/latest/` of cms-omi.readthedocs.io (the owner confirms both paths before release).

## Goals / Non-Goals

**Goals:** one visible entry on every logged in page; the address is configurable and can be hidden; Docker deployments can set it.

**Non-Goals:** see "Not built" in the proposal.

## Decisions

- **Sidebar entry, not a card on the Overview or an item of the corner menu.** One `<li>` after the two menu variants, so it shows in both and needs no CSS. A card would only show on the Overview, move the live tables down and need a dismiss; the corner menu is spec'd for personal controls.
- **The handler computes the final address, the template reads one parameter.** `render_params` adds `manual_url` (empty when hidden). A page rendered without it (the existing test helpers) simply has no entry. A helper at module level does the substitution so it is testable alone.
- **`str.replace("{language}", ...)`, not `str.format`**, so an address with other braces does not raise.
- **The manual language is `es` or `en`.** `shortcut: the manual exists in es and en only`; any other AWS language gets the English manual instead of a 404. Extend when a translation is published.
- **Option in `[admin_web_server]`**, not in the shared web-server section: CWS and RWS get no link.
- **Default is `latest`**, the path README and SUPPORT already use; an operator can pin another one.
- **No validation of the address.** It is the operator's own configuration file; the template escapes it in the attribute.
- **Docker: `off` hides, empty means default.** The generator treats an empty variable as unset for every variable; an operator who uncomments `CMS_MANUAL_URL=` without a value must not lose the link by accident.
- **`target="_blank" rel="noopener noreferrer"`**: admin addresses carry contest and user ids, which must not reach the manual's host in a Referer header.

## Risks / Trade-offs

- [The English path of the published manual may differ] -> only the default string changes; the operator can override it meanwhile.
- [The catalog files conflict with other AWS work] -> regenerate them with the documented commands instead of merging by hand.
