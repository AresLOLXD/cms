#!/usr/bin/env python3

"""A tornado.web.StaticFileHandler variant that searches an ordered list
of directories for each requested path, serving the first match found in
*reverse* location order (later-registered locations override earlier
ones for the same relative path) -- reproducing the "later added,
higher priority" behavior of Werkzeug's SharedDataMiddleware, which the
pre-Tornado-native WebService used for static/doc file serving.

"""

import os

import tornado.web


class MultiLocationStaticFileHandler(tornado.web.StaticFileHandler):
    """Serve static files from the first matching directory in a list.

    Directories are checked in reverse of the order given to
    make_route() -- i.e. a later directory in the list overrides an
    earlier one for the same relative path, matching
    SharedDataMiddleware's existing convention (callers that want a
    package's own static files to take priority over a shared default
    list that package's directory last).

    """

    def initialize(self, locations: list[str], path: str = "") -> None:
        # StaticFileHandler.initialize() normally takes a single `path`
        # and stores it as self.root; validate_absolute_path() then
        # requires the resolved absolute_path to live under that root.
        # We instead resolve the actual matching location per-request in
        # get_absolute_path() and store it as self.root there, so
        # validate_absolute_path()'s containment check still applies
        # (against whichever location matched). `path` is accepted (and
        # ignored) only because RequestHandler.__init__() passes every
        # handler kwarg from the route registration -- including the
        # `path` placeholder make_route() sets -- straight through to
        # initialize(), which would otherwise reject it as unexpected.
        self.locations = list(reversed(locations))
        self.default_filename = None
        # get() reads self.root before calling get_absolute_path(), which
        # is where we actually set it to the matching location; give it
        # a placeholder so that first read doesn't raise AttributeError.
        self.root = ""

    def get_absolute_path(self, root: str, path: str) -> str:
        # Note: StaticFileHandler.get_absolute_path is a classmethod in
        # the base class, but it is only ever invoked here as
        # `self.get_absolute_path(...)` (see StaticFileHandler.get()),
        # so overriding it as a regular instance method works: Python
        # resolves `self.get_absolute_path` to this subclass's method
        # and binds `self` automatically. (The one other call site,
        # `make_static_url`, invokes it as `cls.get_absolute_path(...)`
        # and isn't used by this handler.)
        for location in self.locations:
            candidate = os.path.abspath(os.path.join(location, path))
            if os.path.isfile(candidate):
                # Set self.root to the matching location so that
                # validate_absolute_path()'s containment check (which
                # reads self.root, not the `root` argument) passes.
                self.root = location
                return candidate
        # No location has it: fall back to the last (lowest-priority)
        # location so validate_absolute_path()'s os.path.exists() check
        # correctly produces a 404.
        self.root = self.locations[-1] if self.locations else ""
        return os.path.abspath(os.path.join(self.root, path))

    @classmethod
    def make_route(
        cls, url_pattern: str, locations: list[str]
    ) -> tuple[str, type, dict]:
        """Build a Tornado route entry for this handler.

        url_pattern: the URL regex, with one capture group for the
            relative file path (e.g. r"/static/(.*)").
        locations: directories to search, in "later overrides earlier"
            order.

        return: a (pattern, handler_class, kwargs) tuple usable directly
            in a tornado.web.Application's handlers list.

        """
        return (url_pattern, cls, {"path": "", "locations": locations})
