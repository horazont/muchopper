import asyncio
import logging
import logging.config
import pathlib
import sys

import toml

import aioxmpp

import muchopper.common.model
import muchopper.bot.daemon
import muchopper.bot.state


DEFAULT_CONFIG_PATH = pathlib.Path("/etc/muchopper/config.toml")


async def amain(loop, args, cfg):
    try:
        statefile = pathlib.Path(cfg["muchopping"]["statefile"])
    except KeyError:
        engine = muchopper.common.model.get_generic_engine(
            cfg["muchopping"]["db_uri"]
        )
    else:
        engine = muchopper.common.model.get_sqlite_engine(statefile)

    limits = cfg["muchopping"].get("limits", {})

    state = muchopper.bot.state.State(
        engine,
        pathlib.Path(cfg["muchopping"]["logfile"]),
        int(limits.get("max_name_length", 1024)),
        int(limits.get("max_description_length", 1024)),
        int(limits.get("max_subject_length", 1024)),
        int(limits.get("max_language_length", 126)),
    )

    components = list(map(
        muchopper.bot.daemon.Component,
        cfg["muchopping"].get("components", ["watcher", "scanner"])
    ))

    daemon = muchopper.bot.daemon.MUCHopper(
        loop,
        aioxmpp.JID.fromstr(cfg["xmpp"]["jid"]),
        aioxmpp.make_security_layer(
            cfg["xmpp"]["password"],
        ),
        cfg["muchopping"]["nickname"],
        state,
        list(map(aioxmpp.JID.fromstr,
                 cfg["muchopping"].get("privileged_entities", []))),
        components,
    )

    for addr in cfg["muchopping"].get("seed", []):
        state.require_domain(
            addr,
            seen=False,
        )

    try:
        await daemon.run()
    finally:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--config",
        type=argparse.FileType("r"),
        default=None,
    )

    args = parser.parse_args()

    if args.config is None:
        try:
            args.config = DEFAULT_CONFIG_PATH.open("r")
        except OSError as exc:
            print("failed to open config: {}".format(
                exc,
            ))
            sys.exit(1)

    with args.config:
        cfg = toml.load(args.config)

    logging_cfg = cfg.get("logging", {})
    if logging_cfg.setdefault("version", 1) == 1:
        logging_cfg.setdefault("formatters", {}).setdefault(
            "default", {}
        ).setdefault(
            "format",
            "%(levelname)-8s %(name)-15s %(message)s"
        )
        handler_cfg = logging_cfg.setdefault("handlers", {}).setdefault(
            "default", {}
        )
        handler_cfg.setdefault("class", "logging.StreamHandler")
        handler_cfg.setdefault("formatter", "default")
        handler_cfg.setdefault("level", "NOTSET")

        root_cfg = logging_cfg.setdefault("root", {})
        root_cfg.setdefault("level", "DEBUG")
        root_cfg.setdefault("handlers", ["default"])

    # logging.basicConfig(level=logging.DEBUG)
    print(logging_cfg)
    logging.config.dictConfig(logging_cfg)

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(amain(loop, args, cfg))
    finally:
        loop.close()
