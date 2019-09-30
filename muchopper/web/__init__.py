import babel
import contextlib
import collections
import functools
import gzip
import html
import math
import numbers
import os
import pathlib
import re
import shlex
import tempfile

from datetime import datetime, timedelta

import jinja2

import sqlalchemy

import prometheus_client
import prometheus_client.core
import prometheus_client.exposition

import aioxmpp

import werkzeug

from flask import (
    Flask, render_template, redirect, url_for, request, abort, jsonify,
    send_file, Response,
)
from flask_sqlalchemy import SQLAlchemy, Pagination, BaseQuery
from flask_menu import register_menu, Menu

from ..common import model, queries

from . import colour

app = Flask(__name__)
app.config.from_envvar("MUCHOPPER_WEB_CONFIG")
db = SQLAlchemy(app, metadata=model.Base.metadata)
main_menu = Menu(app)


try:
    from aioxmpp import jid_unescape
except ImportError:
    ESCAPABLE_CODEPOINTS = " \"&'/:<>@"

    # This is the jid_unescape implementation as stolen from aioxmpp.
    def jid_unescape(localpart):
        s = localpart

        for cp in ESCAPABLE_CODEPOINTS:
            s = s.replace("\\{:02x}".format(ord(cp)), cp)

        for cp in ESCAPABLE_CODEPOINTS + "\\":
            s = s.replace(
                "\\5c{:02x}".format(ord(cp)),
                "\\{:02x}".format(ord(cp)),
            )

        return s


def abort_json(status_code, payload):
    resp = jsonify(payload)
    resp.status_code = status_code
    return abort(resp)


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

KNOWN_SERVICE_TYPES = {
    ("server", "im"): "server.im",
    ("conference", "text"): "conference.text",
    ("conference", "irc"): "gateway.irc",
    ("store", "file"): "store.file",
    ("pubsub", "service"): "pubsub.service",
    ("proxy", "bytestreams"): "proxy.ft",
}


PROMETHEUS_METRIC_ROOM_PAGE_HTML = prometheus_client.Summary(
    "muclumbus_http_room_page_html_request_seconds",
    "Time to process a HTML room page request"
)

PROMETHEUS_METRIC_SEARCH_HTML = prometheus_client.Summary(
    "muclumbus_http_search_html_request_seconds",
    "Time to process a HTML search request"
)

PROMETHEUS_METRIC_ROOM_PAGE_API = prometheus_client.Summary(
    "muclumbus_http_room_page_api_request_seconds",
    "Time to process an API room page request"
)

PROMETHEUS_METRIC_ROOM_PAGE_UNSAFE_API = prometheus_client.Summary(
    "muclumbus_http_room_page_unsafe_api_request_seconds",
    "Time to process an unsafe API room page request"
)

PROMETHEUS_METRIC_STATS_HTML = prometheus_client.Summary(
    "muclumbus_http_stats_html_request_seconds",
    "Time to process a HTML stats request"
)

PROMETHEUS_METRIC_SEARCH_API = prometheus_client.Summary(
    "muclumbus_http_search_api_request_seconds",
    "Time to process an API search request"
)

PROMETHEUS_METRIC_BADGE_API = prometheus_client.Summary(
    "muclumbus_http_badge_api_request_seconds",
    "Time to process an API badge request"
)


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


def static_content(generator, path, mimetype):
    if app.debug:
        return generator()

    try:
        static_path = pathlib.Path(app.config["STATIC_PAGE_CACHE"])
    except KeyError:
        return generator()

    rendered_path = (static_path / path).absolute()
    # basic escape check
    if not str(rendered_path).startswith(str(static_path)):
        return generator()

    if (rendered_path in STATIC_RENDERED and
            rendered_path.is_file()):
        return send_file(str(rendered_path),
                         mimetype=mimetype,
                         add_etags=CACHE_USE_ETAGS,
                         conditional=True,
                         as_attachment=False)

    response = generator()
    if isinstance(response, werkzeug.BaseResponse):
        content = b"".join(response.response)
        response.response = [content]
    elif isinstance(response, str):
        content = response.encode("utf-8")
    else:
        content = response

    try:
        with safe_writer(rendered_path, mode="wb") as f:
            f.write(content)
        with safe_writer(
                rendered_path.with_name(rendered_path.name + ".gz"),
                mode="wb") as f:
            with gzip.GzipFile(fileobj=f, mode="wb") as zf:
                zf.write(content)
    except IOError:
        pass
    else:
        STATIC_RENDERED.add(rendered_path)
        return send_file(str(rendered_path),
                         mimetype=mimetype,
                         conditional=True,
                         as_attachment=False)

    return response


def render_static_template(path):
    return static_content(functools.partial(render_template, path),
                          path,
                          "text/html")


@app.template_filter("highlight")
def highlight(s, keywords):
    s = str(s)
    if not keywords:
        return jinja2.Markup(html.escape(s))

    keyword_re = re.compile("|".join(map(re.escape, keywords)), re.I)
    prev_end = 0
    parts = []
    for match in keyword_re.finditer(s):
        start, end = match.span()
        parts.append(html.escape(s[prev_end:start]))
        parts.append("<span class='search-match'>")
        parts.append(html.escape(s[start:end]))
        parts.append("</span>")
        prev_end = end
    parts.append(html.escape(s[prev_end:]))

    return jinja2.Markup("".join(parts))


@app.template_filter("force_escape")
def force_escape(s):
    s = str(s)
    return jinja2.Markup(html.escape(s))


@app.template_filter("jid_unescape")
def jid_unescape_filter(s):
    if s is None:
        return s
    return jid_unescape(s)


@app.template_filter("pretty_number_info")
def pretty_number_info(n):
    if n < 0.5:
        return {
            "short": "0",
            "accessible": "0",
        }

    order_of_magnitude = math.floor(math.log(n, 10))
    scale_info = [
        ("", "{}", 1, ".0", 10**0),
        ("", "{}", 2, ".0", 10**0),
        ("", "{}", 3, ".0", 10**0),
        ("k", "{} thousand", 1, ".0", 10**-3),

        # the below are an alternative for longer numbers, but they break
        # the layout. for now, we replace anything beyond 9999 with ∞.
        # ("k", "{} thousand", 2, ".1", 10**-3),
        # ("k", "{} thousand", 2, ".0", 10**-3),
        # ("k", "{} thousand", 3, ".0", 10**-3),
        # ("M", "{} million", 2, ".1", 10**-6),
        # ("M", "{} million", 2, ".0", 10**-6),
        # ("M", "{} million", 3, ".0", 10**-6),
    ]

    try:
        (suffix, a11y_format, significant_digits,
         float_format, display_factor) = scale_info[order_of_magnitude]
    except IndexError:
        # ?! That’s a friggin’ large number (>= 1 billion)
        # sorry, Ge0rG
        return {
            "short": "∞",
            "accessible": "a very large number of",
        }

    rounding_factor = 10**order_of_magnitude
    rounded_number = round(
        n / rounding_factor, significant_digits - 1
    ) * rounding_factor

    formatted_number = "{{:{}f}}".format(float_format).format(
        rounded_number * display_factor
    )
    short_text = "{}{}".format(formatted_number, suffix)
    a11y_text = a11y_format.format(formatted_number)

    return {
        "short": short_text,
        "accessible": a11y_text,
    }


@app.template_filter("prettify_number")
def prettify_number(n):
    info = pretty_number_info(n)
    return jinja2.Markup(
        "<span aria-hidden='true'>{}</span>"
        "<span class='a11y-text'>{}</span>".format(
            html.escape(info["short"]),
            html.escape(info["accessible"]),
        )
    )


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


@app.template_filter("ccg_rgb_triplet")
def ccg_rgb_triplet(s):
    r, g, b = colour.text_to_colour(str(s))
    r *= 255
    g *= 255
    b *= 255
    return ", ".join("{:.0f}".format(v) for v in (r, g, b))


@app.route("/")
def index():
    return redirect(url_for("room_list", pageno=1))


def room_page(page, per_page, **kwargs):
    q = queries.common_query(db.session, **kwargs)
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
@PROMETHEUS_METRIC_ROOM_PAGE_HTML.time()
def room_list(pageno=1):
    per_page = 25
    page = room_page(pageno, per_page, with_avatar_flag=True)

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


@app.route("/avatar/v1/<address>")
def avatar_v1(address):
    try:
        address = aioxmpp.JID.fromstr(address)
    except (ValueError, TypeError):
        return abort(400, "bad address")

    if request.if_modified_since is not None:
        # quick check if we can early-out
        not_modified = db.session.query(
            model.Avatar.address,
        ).filter(
            model.Avatar.address == address
        ).filter(
            model.Avatar.last_updated <= request.if_modified_since
        ).one_or_none()
        if not_modified:
            return "", 304

    q = db.session.query(
        model.Avatar,
    ).filter(
        model.Avatar.address == address
    )
    avatar = q.one_or_none()
    if avatar is None:
        return abort(404, "no avatar stored")

    response = Response(
        avatar.data,
        mimetype=avatar.mime_type,
    )
    response.last_modified = avatar.last_updated
    response.headers["Content-Security-Policy"] = \
        "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'"
    return response


@app.route("/search")
@register_menu(app, "data.search", "Search", order=2)
@PROMETHEUS_METRIC_SEARCH_HTML.time()
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
                with_avatar_flag=True,
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


def get_metrics():
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

    stale_threshold = datetime.utcnow() - timedelta(days=1)

    ndomains, ndomains_stale = db.session.query(
        sqlalchemy.func.count(),
        sqlalchemy.func.sum(
            sqlalchemy.case(
                [
                    (model.Domain.last_seen < stale_threshold, 1),
                ],
                else_=0
            )
        )
    ).select_from(
        model.Domain,
    ).one()

    return dict(
        nmucs=nmucs,
        npublicmucs=npublicmucs,
        nopenmucs=nopenmucs,
        nhiddenmucs=nhiddenmucs,
        nusers=nusers,
        ndomains=ndomains,
        ndomains_stale=ndomains_stale,
    )


@app.route("/stats")
@register_menu(app, "data.stats", "Statistics", order=4)
@PROMETHEUS_METRIC_STATS_HTML.time()
def statistics():
    common_metrics = get_metrics()

    f = sqlalchemy.func.count().label("count")

    softwares = list(db.session.query(
        model.Domain.software_name,
        f,
    ).group_by(
        model.Domain.software_name,
    ).filter(
        model.Domain.software_name != None  # NOQA
    ).order_by(
        f.desc(),
        model.Domain.software_name.asc(),
    ))

    total_software_info = sum(count for _, count in softwares)
    other_software_info = sum(count for _, count in softwares
                              if count < 3)

    service_counter = collections.Counter()
    unknown_service_types = 0

    for category, type_, instances in db.session.query(
                model.DomainIdentity.category,
                model.DomainIdentity.type_,
                f
            ).group_by(
                model.DomainIdentity.category,
                model.DomainIdentity.type_,
            ):
        try:
            mapped_type = KNOWN_SERVICE_TYPES[category, type_]
        except KeyError:
            unknown_service_types += instances
            continue

        service_counter[mapped_type] += instances

    pruned_softwares = [
        (name, occ, colour.text_to_colour(name))
        for name, occ in softwares
        if occ >= 3
    ]
    pruned_softwares.append(("Other", other_software_info, (0.8, 0.8, 0.8)))
    software_version_chart_sum = sum(occ for _, occ, *_ in pruned_softwares)
    if software_version_chart_sum <= 0:
        software_version_chart_sum = 1

    software_version_chart_cfg = {
        "labels": [name for name, _, _ in pruned_softwares],
        "datasets": [{
            "label": "Occurences",
            "data": [occ for _, occ, _ in pruned_softwares],
            "backgroundColor": [
                "rgba({:.0f}, {:.0f}, {:.0f}, 0.8)".format(
                    r*255, g*255, b*255
                )
                for _, _, (r, g, b) in pruned_softwares
            ],
            "borderColor": [
                "rgba({:.0f}, {:.0f}, {:.0f}, 1.0)".format(
                    r*255, g*255, b*255
                )
                for _, _, (r, g, b) in pruned_softwares
            ],
            "borderWidth": 1
        }]
    }

    return render_template(
        "stats.html",
        softwares=softwares,
        total_software_info=total_software_info,
        other_software_info=other_software_info,
        services=service_counter,
        unknown_service_types=unknown_service_types,
        software_version_chart_cfg=software_version_chart_cfg,
        software_version_chart_sum=software_version_chart_sum,
        **common_metrics,
    )


@app.route("/docs/faq")
@register_menu(app, "docs.faq", "Frequent Questions (FAQ)", order=1)
def faq():
    return render_static_template("faq.html")


@app.route("/docs/owners")
@register_menu(app, "docs.owners", "For room owners", order=2)
def owners():
    return render_static_template("for_owners.html")


@app.route("/docs/operators")
@register_menu(app, "docs.operators", "For service operators", order=3)
def operators():
    return render_static_template("for_operators.html")


@app.route("/docs/api")
@register_menu(app, "docs.developers", "For developers", order=4)
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


@app.route("/legal")
@register_menu(app, "meta.legal", "Legal notes & Contact", order=4)
def legal():
    return render_static_template("legal.html")


@app.route("/contact")
def contact():
    return redirect(url_for('legal'))


# API


def room_to_json(muc, public_info):
    result = {
        "address": str(muc.address),
        "nusers": round(muc.nusers_moving_average),
        "is_open": muc.is_open,
        "name": public_info.name,
        "description": public_info.description,
        "language": public_info.language,
    }
    if muc.anonymity_mode is not None:
        result["anonymity_mode"] = muc.anonymity_mode.value
    return result


@app.route("/api/1.0/rooms.json")
@app.route("/api/1.0/rooms/unsafe")
@PROMETHEUS_METRIC_ROOM_PAGE_UNSAFE_API.time()
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
@PROMETHEUS_METRIC_ROOM_PAGE_API.time()
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


@app.route("/api/1.0/search", methods=["POST"])
@PROMETHEUS_METRIC_SEARCH_API.time()
def api_search():
    payload = request.get_json()
    if payload is None:
        return abort_json(400, {"error": "must POST JSON"})

    try:
        keywords = payload["keywords"]
        search_address = payload.get("sinaddr", True) is True
        search_description = payload.get("sindescr", True) is True
        search_name = payload.get("sinname", True) is True
        min_users = payload.get("min_users", 0)
        after = payload.get("after", None)
    except KeyError as e:
        return abort_json(
            400,
            {
                "error": "key {!s} is required".format(str(e)),
            }
        )

    if not isinstance(min_users, numbers.Real):
        return abort_json(
            400,
            {
                "error": "not a valid numeric value for min_users: {!r}".format(
                    min_users
                )
            }
        )

    if after is not None and not isinstance(after, float):
        return abort_json(
            400,
            {
                "error": "invalid value for after: {!r}".format(after),
            }
        )

    if not search_address and not search_description and not search_name:
        return abort(
            400,
            {
                "error": "search scope is empty"
            }
        )

    if isinstance(keywords, str):
        prepped_keywords = queries.prepare_keywords(keywords)
    elif isinstance(keywords, list):
        prepped_keywords = queries.filter_keywords(keywords, min_length=3)
    else:
        return abort(
            400,
            {
                "error": "keywords must be a string or an array"
            }
        )

    if len(prepped_keywords) > 5:
        return abort(
            400,
            {
                "error": "too many words",
            }
        )

    q = queries.common_query(
        db.session,
        min_users=0,
    )
    if after is not None:
        q = q.filter(
            model.MUC.nusers_moving_average < after
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

    more = len(results) > 100

    items = []
    last_key = None
    for muc, public_info in results[:100]:
        anonymity_mode = None
        if muc.anonymity_mode is not None:
            anonymity_mode = muc.anonymity_mode.value
        items.append({
            "address": str(muc.address),
            "is_open": muc.is_open,
            "nusers": round(muc.nusers_moving_average),
            "description": public_info.description,
            "name": public_info.name,
            "language": public_info.language,
            "anonymity_mode": anonymity_mode,
        })
        last_key = muc.nusers_moving_average

    result = {
        "query": {
            "keywords": list(prepped_keywords),
            "sinaddr": search_address,
            "sindescr": search_description,
            "sinname": search_name,
            "min_users": min_users,
            "after": after,
        },
        "result": {
            "last": last_key,
            "more": more,
            "items": items
        }
    }

    return jsonify(result)


@app.route("/api/1.0/badge")
@PROMETHEUS_METRIC_BADGE_API.time()
def api_badge():
    CHARWIDTH = 7

    try:
        room_jid = request.args["address"]
    except KeyError:
        return abort(400, "missing address argument")

    room_info = db.session.query(
        model.MUC,
        model.PubliclyListedMUC,
    ).join(
        model.PubliclyListedMUC,
    ).filter(
        model.MUC.address == room_jid,
    ).one_or_none()

    if room_info is None or room_info[1] is None:
        return abort(404, "no such room")

    muc, public_info = room_info
    label = " {} ".format(public_info.name or muc.address)
    nusers = " {:.0f} ".format(muc.nusers_moving_average)

    labelwidth = len(label) * CHARWIDTH
    countwidth = len(nusers) * CHARWIDTH
    width = labelwidth + countwidth

    rendered = render_template(
        "badge.svg",
        width=width,
        label=label,
        labelwidth=labelwidth,
        number=nusers,
        countwidth=countwidth,
    )
    return Response(rendered, mimetype="image/svg+xml")


# prometheus export


class MetricCollector:
    def collect(self):
        metrics = get_metrics()

        yield prometheus_client.core.GaugeMetricFamily(
            "muclumbus_http_mucs_total",
            "Number of MUCs known to Muclumbus",
            value=metrics["nmucs"],
        )

        mucs_by_state = prometheus_client.core.GaugeMetricFamily(
            "muclumbus_http_mucs_state_total",
            "Number of MUCs known to Muclumbus, by state",
            labels=["state"]
        )
        mucs_by_state.add_metric(["open"], metrics["nopenmucs"])
        mucs_by_state.add_metric(["public"], metrics["npublicmucs"])
        mucs_by_state.add_metric(["hidden"], metrics["nhiddenmucs"])

        yield mucs_by_state

        yield prometheus_client.core.GaugeMetricFamily(
            "muclumbus_http_occupants_total",
            "Number of occupants summed over all MUCs",
            value=metrics["nusers"],
        )

        yield prometheus_client.core.GaugeMetricFamily(
            "muclumbus_http_domains_total",
            "Number of domains known to Muclumbus",
            value=metrics["ndomains"],
        )

        domains_by_state = prometheus_client.core.GaugeMetricFamily(
            "muclumbus_http_domains_state_total",
            "Number of domains known to Muclumbus, by state",
            labels=["state"]
        )
        domains_by_state.add_metric(["stale"], metrics["ndomains_stale"])

        yield domains_by_state


@app.route("/metrics")
def metrics():
    return Response(
        prometheus_client.exposition.generate_latest(),
        mimetype=prometheus_client.exposition.CONTENT_TYPE_LATEST,
    )


@app.route("/site.manifest")
def site_manifest():
    # this is needed for icons
    generator = functools.partial(
        jsonify,
        {
            "name": "",
            "short_name": "",
            "icons": [
                {
                    "src": url_for("static",
                                   filename="img/android-chrome-192x192.png"),
                    "sizes": "192x192",
                    "type": "image/png"
                },
                {
                    "src": url_for("static",
                                   filename="img/android-chrome-512x512.png"),
                    "sizes": "512x512",
                    "type": "image/png"
                }
            ],
            "theme_color": "#ffffff",
            "background_color": "#ffffff",
            "display": "standalone"
        }
    )

    return static_content(generator, "site.manifest", "application/json")


@app.route("/favicon.ico")
def favicon():
    # fallback resource
    return app.send_static_file('img/favicon.ico')


prometheus_client.core.REGISTRY.register(MetricCollector())
