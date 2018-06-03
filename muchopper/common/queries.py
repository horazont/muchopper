import shlex

import sqlalchemy

from . import model


def base_query(session, *,
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

    return q


def common_query(session, *,
                 min_users=1,
                 include_closed=False):
    q = base_query(session, include_closed=include_closed)

    if min_users > 0:
        q = q.filter(
            model.MUC.nusers_moving_average > min_users
        )

    return q.order_by(
        model.MUC.nusers_moving_average.desc(),
        model.MUC.address.asc(),
    )


def chain_condition(conditional, new):
    if conditional is None:
        return new
    return sqlalchemy.or_(conditional, new)


def prepare_keywords(query_string, min_length=3):
    keywords = shlex.split(query_string)
    keywords = set(
        keyword
        for keyword in (
            keyword.strip()
            for keyword in keywords
        )
        if len(keyword) >= min_length
    )
    return keywords


def apply_search_conditions(q,
                            keywords,
                            search_address,
                            search_description,
                            search_name):
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

    return q
