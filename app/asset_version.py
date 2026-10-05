# A single version string for the app's own CSS and JavaScript files, computed
# once when the app starts up from the newest "last modified" time among them.
#
# Plain-language reason this exists: every page loads the same
# "/static/css/style.css" and "/static/js/....js" files. Browsers keep
# (cache) those files once they have fetched them, and a browser can go on
# using an OLD copy even after the file on disk has been fixed. That is what
# made the Projects cards look "broken" on some tabs (CSS), and what made an
# old copy of the notification script keep running after it was updated (JS)
# -- see PROJECT_LOG.md.
#
# The fix: base.html (and the few pages with their own script) link to
# "/static/css/style.css?v=<this value>" and "/static/js/x.js?v=<this value>".
# A browser treats a different address as a different file, so the moment any
# CSS or JavaScript file is edited and the server is restarted, every browser
# is forced to fetch fresh copies, with no manual hard-refresh ever needed.
import os

_STATIC = os.path.join(os.path.dirname(__file__), "static")


def _newest_asset_time():
    newest = 0
    for folder, extension in (("css", ".css"), ("js", ".js")):
        directory = os.path.join(_STATIC, folder)
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.endswith(extension):
                continue
            try:
                newest = max(newest, int(os.path.getmtime(os.path.join(directory, name))))
            except OSError:
                pass
    return newest


# Never crash the whole app over a cache-buster: fall back to a fixed value.
ASSET_VERSION = str(_newest_asset_time() or 1)


def register_asset_version(templates):
    """Adds the `asset_v` global to one Jinja2Templates instance, used in
    base.html as "/static/css/style.css?v={{ asset_v }}". Called once per
    Jinja2Templates() instance across the app (see each routes.py module
    and main.py) -- same pattern as time_utils.register_localtime."""
    templates.env.globals["asset_v"] = ASSET_VERSION
