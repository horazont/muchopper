#!/usr/bin/env python3
import asyncio
import getpass
import logging
import shlex

import aioxmpp
import aioxmpp.rsm.xso

import muchopper.bot.xso as xso


def print_item(item):
    flags = []
    if not item.anonymity_mode or item.anonymity_mode.value != "semi":
        flags.append("non-anon")

    print("{:4d} {!r} ({}){}{}".format(
        item.nusers,
        item.name,
        item.address,
        " " if flags else "",
        " ".join(flags),
    ))
    if item.description or item.language:
        parts = ["     "]
        if item.description:
            parts.append(item.description)
        if item.language:
            if parts:
                parts.append("  ")
            parts.append("(primary language: {})".format(item.language))
        print("".join(parts))


async def amain(args, password):
    client = aioxmpp.Client(
        args.local_jid,
        aioxmpp.make_security_layer(password)
    )

    async with client.connected() as stream:
        form_xso = (await stream.send(
            aioxmpp.IQ(
                to=args.service_jid,
                type_=aioxmpp.IQType.GET,
                payload=xso.Search()
            )
        )).form
        form_obj = xso.SearchForm.from_xso(form_xso)

        form_obj.query.value = " ".join(map(shlex.quote, args.query))
        form_obj.order_by.value = args.order_by

        if args.min_users is not None:
            form_obj.min_users.value = args.min_users

        request = xso.Search()
        request.form = form_obj.render_reply()
        request.rsm = aioxmpp.rsm.xso.ResultSetMetadata()

        if args.request_page_size is not None:
            request.rsm.max_ = args.request_page_size

        nresults = 0
        while args.fetch_up_to is None or nresults < args.fetch_up_to:
            reply = await stream.send(aioxmpp.IQ(
                to=args.service_jid,
                type_=aioxmpp.IQType.GET,
                payload=request
            ))
            for item in reply.items:
                print_item(item)

            nresults += len(reply.items)
            if len(reply.items) < reply.rsm.max_:
                break

            request.rsm.after = aioxmpp.rsm.xso.After()
            request.rsm.after.value = reply.rsm.last.value


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--search-address",
        dest="search_address",
        default=True,
        action="store_true",
    )

    parser.add_argument(
        "--no-search-address",
        dest="search_address",
        action="store_false",
    )

    parser.add_argument(
        "--search-name",
        dest="search_name",
        default=True,
        action="store_true",
    )

    parser.add_argument(
        "--no-search-name",
        dest="search_name",
        action="store_false",
    )

    parser.add_argument(
        "--search-description",
        dest="search_description",
        default=True,
        action="store_true",
    )

    parser.add_argument(
        "--no-search-description",
        dest="search_description",
        action="store_false",
    )

    parser.add_argument(
        "--request-page-size",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--min-users",
        type=int,
        default=None,
    )

    parser.add_argument(
        "-n", "--fetch-up-to",
        type=int,
        default=None,
    )

    parser.add_argument(
        "-s", "--order-by",
        default="nusers",
    )

    parser.add_argument(
        "local_jid",
        type=aioxmpp.JID.fromstr,
    )

    parser.add_argument(
        "service_jid",
        type=aioxmpp.JID.fromstr,
    )

    parser.add_argument(
        "query",
        nargs="*",
    )

    parser.add_argument(
        "-v",
        action="count",
        default=0,
        dest="verbosity",
        help="Increase verbosity (up to -vvv)"
    )

    args = parser.parse_args()
    password = getpass.getpass("Password for {}:".format(args.local_jid))

    logging.basicConfig(
        level={
            0: logging.ERROR,
            1: logging.WARNING,
            2: logging.INFO,
        }.get(args.verbosity, logging.DEBUG),
    )

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(amain(args, password))
    finally:
        loop.close()
