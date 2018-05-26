import collections

from flask import Flask, render_template, redirect, url_for
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


@app.route("/")
def index():
    return redirect(url_for("room_list", page=1))


@app.route("/rooms/")
@app.route("/rooms/<int:page>")
@register_menu(app, "rooms", "Chat Room index")
def room_list(page=1):
    q = db.session.query(model.MUC, model.PubliclyListedMUC).join(
        model.PubliclyListedMUC,
    ).filter(
        model.MUC.nusers > 1
    ).order_by(
        model.MUC.nusers.desc(),
        model.MUC.address.asc(),
    )
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


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/about")
@register_menu(app, "about", "About")
def about():
    return render_template("about.html")
