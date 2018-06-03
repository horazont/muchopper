import asyncio

import aioxmpp
import aioxmpp.forms
import aioxmpp.service
import aioxmpp.rsm.xso
import aioxmpp.xso

from aioxmpp.utils import namespaces

from ..common import queries, model

from . import utils, xso


class Spokesman(utils.MuchopperService, aioxmpp.service.Service):
    ORDER_AFTER = [
        aioxmpp.disco.DiscoServer,
    ]

    search_feature = aioxmpp.disco.register_feature(
        namespaces.net_zombofant_muclumbus_search
    )

    rsm_feature = aioxmpp.disco.register_feature(
        namespaces.xep0059_rsm
    )

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._helper_funcs = {
            "nusers": (
                self._base_query_nusers,
                self._key_nusers,
            ),
            "address": (
                self._base_query_address,
                self._key_address,
            )
        }

    def _base_query_nusers(self, session, after):
        q = queries.base_query(session)
        if after is not None:
            after = float(after)
            q = q.filter(
                model.MUC.nusers_moving_average < after
            )

        q = q.order_by(model.MUC.nusers_moving_average.desc())

        return q

    def _key_nusers(self, muc, public_muc):
        return muc.nusers_moving_average

    def _base_query_address(self, session, after):
        q = queries.base_query(session)
        if after is not None:
            after = aioxmpp.JID.fromstr(after)
            q = q.filter(
                model.MUC.address > after
            )

        q = q.order_by(model.MUC.address.asc())

        return q

    def _key_address(self, muc, public_muc):
        return muc.address

    @aioxmpp.service.iq_handler(aioxmpp.IQType.GET, xso.Search)
    @asyncio.coroutine
    def handle_search(self, request):
        if not self._state_future.done():
            raise aioxmpp.errors.XMPPWaitError(
                (namespaces.stanzas, "internal-server-error"),
                text="Search service not initialised yet"
            )

        request = request.payload
        max_ = 100

        if not request.rsm and not request.form:
            reply = xso.Search()
            reply.form = xso.SearchForm().render_request()
            reply.form.type_ = aioxmpp.forms.DataType.FORM
            return reply

        after = None

        if request.rsm is not None:
            if (request.rsm.before or request.rsm.first or request.rsm.last or
                    request.rsm.index):
                # we don’t support those
                raise aioxmpp.errors.XMPPModifyError(
                    (namespaces.stanzas, "feature-not-implemented"),
                    text="Attempt to use unsupported RSM features"
                )

            if request.rsm.after:
                after = request.rsm.after.value

            if request.rsm.max_ is not None and request.rsm.max_ > 0:
                max_ = max(min(max_, request.rsm.max_), 1)

        if (request.form is None or
                request.form.get_form_type() != xso.SearchForm.FORM_TYPE):
            raise aioxmpp.errors.XMPPModifyError(
                (namespaces.stanzas, "bad-request"),
                text="Form missing or invalid FORM_TYPE"
            )

        try:
            form = xso.SearchForm.from_xso(request.form)
        except (ValueError, TypeError) as exc:
            raise aioxmpp.errors.XMPPModifyError(
                (namespaces.stanzas, "bad-request"),
                text="Failed to parse search form ({})".format(exc)
            )

        try:
            base_query_func, key_func = self._helper_funcs[form.order_by.value]
        except KeyError:
            raise aioxmpp.errors.XMPPModifyError(
                (namespaces.stanzas, "bad-request"),
                text="Invalid key value"
            )

        if len(form.query.value) > 1024:
            raise aioxmpp.errors.XMPPModifyError(
                (namespaces.stanzas, "policy-violation"),
                text="Query too long"
            )

        if form.query.value:
            keywords = queries.prepare_keywords(form.query.value)
            search_address = form.search_address.value
            search_description = form.search_description.value
            search_name = form.search_name.value

            if (not search_address and
                    not search_description and
                    not search_name):
                raise aioxmpp.errors.XMPPModifyError(
                    (namespaces.stanzas, "bad-request"),
                    text="Search scope is empty"
                )

            if not keywords:
                raise aioxmpp.errors.XMPPModifyError(
                    (namespaces.stanzas, "bad-request"),
                    text="No valid search terms"
                )

            if len(keywords) > 5:
                raise aioxmpp.errors.XMPPModifyError(
                    (namespaces.stanzas, "policy-violation"),
                    text="Too many search terms"
                )

            return_all = False
        else:
            return_all = True

        state = self._state

        with state.get_session() as session:
            q = base_query_func(session, after)
            if form.min_users.value and form.min_users.value > 0:
                q = q.filter(
                    model.MUC.nusers_moving_average >= form.min_users.value
                )

            if not return_all:
                q = queries.apply_search_conditions(
                    q,
                    keywords,
                    search_address,
                    search_description,
                    search_name,
                )

            q = q.limit(max_ + 1)
            results = list(q)

            more = len(results) > max_
            del results[max_:]

            reply = xso.SearchResult()
            for muc, public_info in results:
                item_xso = xso.SearchResultItem()
                item_xso.address = muc.address
                item_xso.is_open = muc.is_open
                item_xso.nusers = round(muc.nusers_moving_average)
                item_xso.description = public_info.description
                item_xso.name = public_info.name
                item_xso.language = public_info.language
                reply.items.append(item_xso)

            reply.rsm = aioxmpp.rsm.xso.ResultSetMetadata()
            if results:
                reply.rsm.first = aioxmpp.rsm.xso.First()
                reply.rsm.first.value = str(key_func(*results[-1]))
                reply.rsm.last = aioxmpp.rsm.xso.Last()
                reply.rsm.last.value = str(key_func(*results[-1]))
                reply.rsm.max_ = max_

            session.rollback()

        return reply
