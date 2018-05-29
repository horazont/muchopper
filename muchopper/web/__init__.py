import collections
import html
import re
import shlex

import jinja2

import sqlalchemy

from flask import Flask, render_template, redirect, url_for, request
from flask_sqlalchemy import SQLAlchemy, Pagination, BaseQuery
from flask_menu import register_menu, Menu

from ..common import model

app = Flask(__name__)
app.config.from_envvar("MUCHOPPER_WEB_CONFIG")
db = SQLAlchemy(app, metadata=model.Base.metadata)
Menu(app)


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
    parts.append(s[prev_end:])

    return jinja2.Markup("".join(parts))


@app.route("/")
def index():
    return redirect(url_for("room_list", page=1))


def base_query(session, *,
               min_users=1,
               include_closed=False):
    q = session.query(
        model.MUC,
        model.PubliclyListedMUC
    ).join(
        model.PubliclyListedMUC
    )

    if not include_closed:
        q = q.filter(
            model.MUC.is_open == True  # NOQA
        )

    q = q.filter(
        model.MUC.is_hidden == False  # NOQA
    )

    if min_users > 0:
        q = q.filter(
            model.MUC.nusers_moving_average > min_users
        )

    return q.order_by(
        model.MUC.nusers_moving_average.desc(),
        model.MUC.address.asc(),
    )


@app.route("/rooms/")
@app.route("/rooms/<int:page>")
@register_menu(app, "rooms", "All Rooms", order=1)
def room_list(page=1):
    q = base_query(db.session)
    total = q.count()
    per_page = 25
    pages = (total+per_page-1) // per_page
    visible_pages = \
        set(range(max(1, page-2), min(page+2, pages)+1)) | \
        set(range(1, min(2, pages)+1)) | \
        set(range(max(1, pages-1), pages+1))
    visible_pages = sorted(visible_pages)
    page = Page(
        has_prev=page > 1,
        has_next=page < pages,
        page=page,
        pages=pages,
        total=total,
        items=list(q.offset((page-1)*25).limit(25)),
    )

    visible_pages = [
        (pageno, prev + 1 != pageno)
        for pageno, prev in zip(visible_pages, [0]+visible_pages)
    ]

    return render_template("room_list.html", page=page,
                           visible_pages=visible_pages)


def chain_condition(conditional, new):
    if conditional is None:
        return new
    return sqlalchemy.or_(conditional, new)


def perform_search(query_string,
                   search_address,
                   search_description,
                   search_name):
    if not (search_address or search_description or search_name):
        return ({"no_keywords"}, None, None)

    keywords = shlex.split(query_string)
    keywords = set(
        keyword
        for keyword in (
            keyword.strip()
            for keyword in keywords
        )
        if len(keyword) >= 3
    )

    if len(keywords) > 5:
        return ({"too_many_keywords"}, None, None)
    elif not keywords:
        return ({"no_keywords"}, None, None)

    q = base_query(db.session,
                   min_users=0)
    for keyword in keywords:
        conditional = None
        if search_address:
            conditional = chain_condition(
                conditional,
                model.PubliclyListedMUC.address.ilike("%" + keyword + "%")
            )
        if search_description:
            conditional = chain_condition(
                conditional,
                model.PubliclyListedMUC.description.ilike("%" + keyword + "%")
            )
        if search_name:
            conditional = chain_condition(
                conditional,
                model.PubliclyListedMUC.name.ilike("%" + keyword + "%")
            )
        q = q.filter(conditional)

    q = q.limit(101)
    results = list(q)
    if len(results) > 100:
        del results[100:]
        return ({"too_many_results"}, results, keywords)

    return ([], results, keywords)


@app.route("/search", methods=["POST", "GET"])
@register_menu(app, "search", "Search", order=2)
def search():
    no_keywords = False
    orig_keywords = ""
    too_many_keywords = False
    results = None
    too_many_results = False
    search_address = True
    search_description = True
    search_name = True

    if request.method == "POST":
        orig_keywords = request.form["keywords"]

        if "full-form" in request.form:
            search_address = "search_address" in request.form
            search_description = "search_description" in request.form
            search_name = "search_name" in request.form

        flags, results, keywords = perform_search(
            orig_keywords,
            search_address,
            search_description,
            search_name,
        )

        no_keywords = "no_keywords" in flags
        too_many_keywords = "too_many_keywords" in flags
        too_many_results = "too_many_results" in flags
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
@register_menu(app, "stats", "Statistics", order=3)
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


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/about")
@register_menu(app, "about", "About", order=4)
def about():
    return render_template("about.html")
