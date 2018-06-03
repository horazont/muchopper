import babel
import contextlib
import collections
import gzip
import html
import os
import pathlib
import re
import shlex
import tempfile

import jinja2

import sqlalchemy

import aioxmpp

from flask import (
    Flask, render_template, redirect, url_for, request, abort, jsonify,
    send_file,
)
from flask_sqlalchemy import SQLAlchemy, Pagination, BaseQuery
from flask_menu import register_menu, Menu

from ..common import model, queries

app = Flask(__name__)
app.config.from_envvar("MUCHOPPER_WEB_CONFIG")
db = SQLAlchemy(app, metadata=model.Base.metadata)
main_menu = Menu(app)


with app.app_context():
    main_menu.root().submenu('data').register(order=0, text="Data")
    main_menu.root().submenu('docs').register(order=1, text="Documentation")
    main_menu.root().submenu('meta').register(order=2, text="Meta")

    try:
        cache_path = pathlib.Path(app.config["STATIC_PAGE_CACHE"])
    except KeyError:
        pass
    else:
        cache_path.mkdir(
            exist_ok=True,
            parents=True,
            mode=0o755
        )
        cache_path.chmod(0o755)

    CACHE_USE_ETAGS = app.config.get("CACHE_USE_ETAGS", False)


Page = collections.namedtuple(
    "Page",
    [
        "page",
        "has_next",
        "has_prev",
        "items",
        "total",
        "pages",
    ]
)


DISPLAY_LOCALE = babel.Locale("en_GB")


@contextlib.contextmanager
def safe_writer(destpath, mode="wb", extra_paranoia=False):
    """
    Safely overwrite a file.

    This guards against the following situations:

    * error/exception while writing the file (the original file stays intact
      without modification)
    * most cases of unclean shutdown (*either* the original *or* the new file
      will be seen on disk)

    It does that with the following means:

    * a temporary file next to the target file is used for writing
    * if an exception is raised in the context manager, the temporary file is
      discarded and nothing else happens
    * otherwise, the temporary file is synced to disk and then used to replace
      the target file.

    If `extra_paranoia` is true, the parent directory of the target file is
    additionally synced after the replacement. `extra_paranoia` is only needed
    if it is required that the new file is seen after a crash (and not the
    original file).
    """

    destpath = pathlib.Path(destpath)
    with tempfile.NamedTemporaryFile(
            mode=mode,
            dir=str(destpath.parent),
            delete=False) as tmpfile:
        try:
            yield tmpfile
        except:  # NOQA
            os.unlink(tmpfile.name)
            raise
        else:
            tmpfile.flush()
            os.fsync(tmpfile.fileno())
            os.replace(tmpfile.name, str(destpath))


STATIC_RENDERED = set()


def render_static_template(path):
    if app.debug:
        return render_template(path)

    try:
        static_path = pathlib.Path(app.config["STATIC_PAGE_CACHE"])
    except KeyError:
        return render_template(path)

    rendered_path = (static_path / path).absolute()
    # basic escape check
    if not str(rendered_path).startswith(str(static_path)):
        return render_template(path)

    if (rendered_path in STATIC_RENDERED and
            rendered_path.is_file()):
        return send_file(str(rendered_path),
                         mimetype="text/html",
                         add_etags=CACHE_USE_ETAGS,
                         conditional=True,
                         as_attachment=False)

    content = render_template(path)

    try:
        with safe_writer(rendered_path, mode="w") as f:
            f.write(content)
        with safe_writer(
                rendered_path.with_name(rendered_path.name + ".gz"),
                mode="wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb") as zf:
                zf.write(content.encode("utf-8"))
    except IOError:
        pass
    else:
        STATIC_RENDERED.add(rendered_path)
        return send_file(str(rendered_path),
                         mimetype="text/html",
                         conditional=True,
                         as_attachment=False)

    return render_template(path)


@app.template_filter("highlight")
def highlight(s, keywords):
    s = str(s)
    if not keywords:
        return jinja2.Markup(html.escape(s))

    keyword_re = re.compile("|".join(map(re.escape, keywords)), re.I)
    prev_end = 0
    parts = []
    for match in keyword_re.finditer(s):
        print(match)
        start, end = match.span()
        parts.append(html.escape(s[prev_end:start]))
        parts.append("<span class='search-match'>")
        parts.append(html.escape(s[start:end]))
        parts.append("</span>")
        prev_end = end
    parts.append(html.escape(s[prev_end:]))

    return jinja2.Markup("".join(parts))


@app.template_filter('prettify_lang')
def prettify_lang(s):
    s = str(s)
    try:
        lang_name, region_name = s.split("-", 2)[:2]
    except ValueError:
        lang_name, region_name = s, None

    options = [
        lang_name,
    ]
    if region_name is not None:
        options.insert(0, "{}_{}".format(lang_name, region_name.upper()))

    for option in options:
        try:
            return babel.Locale(lang_name).get_display_name(DISPLAY_LOCALE)
        except babel.core.UnknownLocaleError:
            pass

    return s


@app.route("/")
def index():
    return redirect(url_for("room_list", pageno=1))


def room_page(page, per_page, include_closed=False):
    q = queries.common_query(db.session, include_closed=include_closed)
    total = q.count()
    pages = (total+per_page-1) // per_page
    page = Page(
        has_prev=page > 1,
        has_next=page < pages,
        page=page,
        pages=pages,
        total=total,
        items=list(q.offset((page-1)*per_page).limit(per_page)),
    )

    return page


@app.route("/rooms/")
@app.route("/rooms/<int:pageno>")
@register_menu(app, "data.rooms", "All Rooms", order=1)
def room_list(pageno=1):
    per_page = 25
    page = room_page(pageno, per_page)

    pages = page.pages
    visible_pages = \
        set(range(max(1, pageno-2), min(pageno+2, pages)+1)) | \
        set(range(1, min(2, pages)+1)) | \
        set(range(max(1, pages-1), pages+1))
    visible_pages = sorted(visible_pages)

    visible_pages = [
        (pageno, prev + 1 != pageno)
        for pageno, prev in zip(visible_pages, [0]+visible_pages)
    ]

    return render_template("room_list.html", page=page,
                           visible_pages=visible_pages)


@app.route("/search")
@register_menu(app, "data.search", "Search", order=2)
def search():
    no_keywords = False
    orig_keywords = ""
    too_many_keywords = False
    results = None
    too_many_results = False
    search_address = True
    search_description = True
    search_name = True

    if "q" in request.args:
        orig_keywords = request.args["q"]

        if "f" in request.args:
            search_address = "sinaddr" in request.args
            search_description = "sindescr" in request.args
            search_name = "sinname" in request.args

        keywords = queries.prepare_keywords(orig_keywords)
        if len(keywords) > 5:
            too_many_keywords = True
        elif not keywords:
            no_keywords = True
        elif (not search_address and
              not search_description and
              not search_name):
            no_keywords = True
        else:
            q = queries.common_query(
                db.session,
                min_users=0,
            )

            q = queries.apply_search_conditions(
                q,
                keywords,
                search_address,
                search_description,
                search_name,
            )

            q = q.limit(101)
            results = list(q)

            if len(results) > 100:
                results = results[:100]
                too_many_results = True
    else:
        keywords = None

    return render_template(
        "search.html",
        no_keywords=no_keywords,
        too_many_keywords=too_many_keywords,
        too_many_results=too_many_results,
        orig_keywords=orig_keywords,
        results=results,
        search_address=search_address,
        search_description=search_description,
        search_name=search_name,
        keywords=keywords,
    )


@app.route("/stats")
@register_menu(app, "data.stats", "Statistics", order=3)
def statistics():
    q = db.session.query(
        sqlalchemy.func.count(),
        sqlalchemy.func.count(model.PubliclyListedMUC.address),
        sqlalchemy.func.count(sqlalchemy.func.nullif(model.MUC.is_open, False)),
        sqlalchemy.func.count(
            sqlalchemy.func.nullif(model.MUC.is_hidden, False)
        ),
        sqlalchemy.func.sum(model.MUC.nusers)
    ).select_from(
        model.MUC,
    ).outerjoin(
        model.PubliclyListedMUC,
    )

    nmucs, npublicmucs, nopenmucs, nhiddenmucs, nusers = q.one()

    ndomains, = db.session.query(
        sqlalchemy.func.count()
    ).select_from(
        model.Domain,
    ).one()

    return render_template(
        "stats.html",
        nmucs=nmucs,
        npublicmucs=npublicmucs,
        nopenmucs=nopenmucs,
        nhiddenmucs=nhiddenmucs,
        nusers=nusers,
        ndomains=ndomains,
    )


@app.route("/docs/owners")
@register_menu(app, "docs.owners", "For room owners", order=1)
def owners():
    return render_static_template("for_owners.html")


@app.route("/docs/operators")
@register_menu(app, "docs.operators", "For service operators", order=2)
def operators():
    return render_static_template("for_operators.html")


@app.route("/docs/api")
@register_menu(app, "docs.developers", "For developers", order=3)
def developers():
    return render_static_template("for_developers.html")


@app.route("/about")
@register_menu(app, "meta.about", "About", order=1)
def about():
    return render_static_template("about.html")


@app.route("/tos")
@register_menu(app, "meta.tos", "Terms of Service", order=2)
def tos():
    return render_static_template("tos.html")


@app.route("/privacy")
@register_menu(app, "meta.privacy", "Privacy Policy", order=3)
def privacy():
    return render_static_template("privacy.html")


@app.route("/contact")
@register_menu(app, "meta.contact", "Contact", order=4)
def contact():
    return render_static_template("contact.html")


# API


def room_to_json(muc, public_info):
    return {
        "address": str(muc.address),
        "nusers": round(muc.nusers_moving_average),
        "is_open": muc.is_open,
        "name": public_info.name,
        "description": public_info.description,
        "language": public_info.language,
    }


@app.route("/api/1.0/rooms.json")
@app.route("/api/1.0/rooms/unsafe")
def api_rooms_unsafe():
    try:
        pageno = int(request.args["p"])
        order_by = request.args.get("order_by", "nusers")
        include_closed = request.args.get("include_closed") is not None
    except ValueError:
        return abort(400)

    if order_by != "nusers":
        return abort(400)

    if pageno <= 0:
        return abort(400)

    page = room_page(pageno, per_page=200, include_closed=include_closed)

    return jsonify({
        "total": page.total,
        "npages": page.pages,
        "page": page.page,
        "items": [
            room_to_json(muc, public_info)
            for muc, public_info in page.items
        ],
    })


def optional_typecast_argument(args, name, type_):
    try:
        value_s = request.args[name]
    except KeyError:
        return None
    else:
        return type_(value_s)


@app.route("/api/1.0/rooms")
def api_rooms_safe():
    PAGE_SIZE = 200

    try:
        after = optional_typecast_argument(request.args, "after",
                                           aioxmpp.JID.fromstr)
        include_closed = request.args.get("include_closed") is not None
        min_users = optional_typecast_argument(
            request.args, "min_users",
            int,
        )
    except ValueError:
        return abort(400)

    q = queries.base_query(db.session, include_closed=include_closed)
    if after is not None:
        q = q.filter(
            model.MUC.address > after
        )
    if min_users is not None:
        q = q.filter(
            model.MUC.nusers_moving_average >= min_users
        )
    q = q.order_by(
        model.MUC.address.asc()
    )

    q = q.limit(PAGE_SIZE)
    results = list(q)

    return jsonify({
        "items": [
            room_to_json(muc, public_info)
            for muc, public_info in results
        ],
    })
