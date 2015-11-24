#####################################################################################
#
#  Copyright (C) Tavendo GmbH
#
#  Unless a separate license agreement exists between you and Tavendo GmbH (e.g. you
#  have purchased a commercial license), the license terms below apply.
#
#  Should you enter into a separate license agreement after having received a copy of
#  this software, then the terms of such license agreement replace the terms below at
#  the time at which such license agreement becomes effective.
#
#  In case a separate license agreement ends, and such agreement ends without being
#  replaced by another separate license agreement, the license terms below apply
#  from the time at which said agreement ends.
#
#  LICENSE TERMS
#
#  This program is free software: you can redistribute it and/or modify it under the
#  terms of the GNU Affero General Public License, version 3, as published by the
#  Free Software Foundation. This program is distributed in the hope that it will be
#  useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
#  See the GNU Affero General Public License Version 3 for more details.
#
#  You should have received a copy of the GNU Affero General Public license along
#  with this program. If not, see <http://www.gnu.org/licenses/agpl-3.0.en.html>.
#
#####################################################################################

from __future__ import absolute_import, division, print_function

from twisted.internet import reactor
from twisted.internet.selectreactor import SelectReactor
from twisted.python.filepath import FilePath
from twisted.trial.unittest import TestCase
from twisted.logger import globalLogPublisher

from crossbar.router.role import RouterRoleStaticAuth, RouterPermissions
from crossbar.worker import router
from crossbar._logging import make_logger

from autobahn.wamp.exception import ApplicationError
from autobahn.wamp.message import Publish, Published, Subscribe, Subscribed
from autobahn.wamp.message import Register, Registered, Hello, Welcome
from autobahn.wamp.role import RoleBrokerFeatures, RoleDealerFeatures
from autobahn.wamp.types import ComponentConfig

from .examples.goodclass import _

log = make_logger()


try:
    from twisted.web.wsgi import WSGIResource  # noqa
    import treq
except (ImportError, SyntaxError):
    WSGI_TESTS = "Twisted WSGI support is not available."
else:
    WSGI_TESTS = False


class DottableDict(dict):
    def __getattr__(self, name):
        return self[name]


class FakeWAMPTransport(object):
    """
    A fake WAMP transport that responds to all messages with successes.
    """
    def __init__(self, session):
        self._messages = []
        self._session = session

    def send(self, message):
        """
        Send the message, respond with it's success message synchronously.
        Append it to C{self._messages} for later analysis.
        """
        self._messages.append(message)

        if isinstance(message, Hello):
            self._session.onMessage(
                Welcome(1, {u"broker": RoleBrokerFeatures(),
                            u"dealer": RoleDealerFeatures()},
                        authrole=u"anonymous"))
        elif isinstance(message, Register):
            self._session.onMessage(
                Registered(message.request, message.request))
        elif isinstance(message, Publish):
            self._session.onMessage(
                Published(message.request, message.request))
        elif isinstance(message, Subscribe):
            self._session.onMessage(
                Subscribed(message.request, message.request))
        else:
            assert False, message

    def _get(self, klass):
        return list(filter(lambda x: isinstance(x, klass), self._messages))


class RouterWorkerSessionTests(TestCase):

    def setUp(self):
        """
        Set up the common component config.
        """
        self.realm = "realm1"
        config_extras = DottableDict({"node": "testnode",
                                      "worker": "worker1",
                                      "cbdir": self.mktemp()})
        self.config = ComponentConfig(self.realm, extra=config_extras)

    def test_basic(self):
        """
        We can instantiate a RouterWorkerSession.
        """
        log_list = []

        r = router.RouterWorkerSession(config=self.config, reactor=reactor)
        r.log = make_logger(observer=log_list.append, log_level="debug")

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        # XXX depends on log-text; perhaps a little flaky...
        self.assertIn("running as", log_list[-1]["log_format"])

    def test_start_router_component(self):
        """
        Starting a class-based router component works.
        """
        r = router.RouterWorkerSession(config=self.config, reactor=reactor)

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        realm_config = {
            u"name": u"realm1",
            u'roles': [{u'name': u'anonymous',
                        u'permissions': [{u'subscribe': True,
                                          u'register': True, u'call': True,
                                          u'uri': u'*', u'publish': True}]}]
        }

        r.start_router_realm("realm1", realm_config)

        permissions = RouterPermissions('', True, True, True, True, True)
        routera = r._router_factory.get(u'realm1')
        routera.add_role(RouterRoleStaticAuth(router, 'anonymous', default_permissions=permissions))

        component_config = {
            "type": u"class",
            "classname": u"crossbar.worker.test.examples.goodclass.AppSession",
            "realm": u"realm1"
        }

        r.start_router_component("newcomponent", component_config)

        self.assertEqual(len(r.get_router_components()), 1)
        self.assertEqual(r.get_router_components()[0]["id"],
                         "newcomponent")

        self.assertEqual(len(_), 1)
        _.pop()  # clear this global state

    def test_start_router_component_fails(self):
        """
        Trying to start a class-based router component that gets an error on
        importing fails.
        """
        log_list = []

        r = router.RouterWorkerSession(config=self.config, reactor=reactor)
        r.log = make_logger(observer=log_list.append, log_level="debug")

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        realm_config = {
            u"name": u"realm1",
            u'roles': [{u'name': u'anonymous',
                        u'permissions': [{u'subscribe': True,
                                          u'register': True, u'call': True,
                                          u'uri': u'*', u'publish': True}]}]
        }

        r.start_router_realm("realm1", realm_config)

        component_config = {
            "type": u"class",
            "classname": u"thisisathing.thatdoesnot.exist",
            "realm": u"realm1"
        }

        with self.assertRaises(ApplicationError) as e:
            r.start_router_component("newcomponent", component_config)

        self.assertIn(
            "Failed to import class 'thisisathing.thatdoesnot.exist'",
            str(e.exception.args[0]))

        self.assertEqual(len(r.get_router_components()), 0)

    def test_start_router_component_invalid_type(self):
        """
        Trying to start a component with an invalid type fails.
        """
        log_list = []

        r = router.RouterWorkerSession(config=self.config, reactor=reactor)
        r.log = make_logger(observer=log_list.append, log_level="debug")

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        realm_config = {
            u"name": u"realm1",
            u'roles': []
        }

        r.start_router_realm("realm1", realm_config)

        component_config = {
            "type": u"notathingcrossbarsupports",
            "realm": u"realm1"
        }

        with self.assertRaises(ApplicationError) as e:
            r.start_router_component("newcomponent", component_config)

        self.assertEqual(e.exception.error, u"crossbar.error.invalid_configuration")

        self.assertEqual(len(r.get_router_components()), 0)

    def test_start_router_component_wrong_baseclass(self):
        """
        Starting a class-based router component fails when the application
        session isn't derived from ApplicationSession.
        """
        r = router.RouterWorkerSession(config=self.config, reactor=reactor)

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        realm_config = {
            u"name": u"realm1",
            u'roles': []
        }

        r.start_router_realm("realm1", realm_config)

        component_config = {
            "type": u"class",
            "classname": u"crossbar.worker.test.examples.badclass.AppSession",
            "realm": u"realm1"
        }

        with self.assertRaises(ApplicationError) as e:
            r.start_router_component("newcomponent", component_config)

        self.assertIn(
            ("session not derived of ApplicationSession"),
            str(e.exception.args[0]))

        self.assertEqual(len(r.get_router_components()), 0)


class WSGITests(TestCase):

    skip = WSGI_TESTS

    def setUp(self):
        self.cbdir = FilePath(self.mktemp())
        self.cbdir.createDirectory()
        config_extras = DottableDict({"node": "testnode",
                                      "worker": "worker1",
                                      "cbdir": self.cbdir.path})
        self.config = ComponentConfig("realm1", extra=config_extras)

    def test_basic(self):
        """
        A basic WSGI app can be ran.
        """
        temp_reactor = SelectReactor()
        logs = []

        globalLogPublisher.addObserver(logs.append)
        self.addCleanup(globalLogPublisher.removeObserver, logs.append)

        r = router.RouterWorkerSession(config=self.config,
                                       reactor=temp_reactor)

        # Open the transport
        transport = FakeWAMPTransport(r)
        r.onOpen(transport)

        realm_config = {
            u"name": u"realm1",
            u'roles': []
        }

        r.start_router_realm("realm1", realm_config)
        r.start_router_transport(
            "component1",
            {
                u"type": u"web",
                u"endpoint": {
                    u"type": u"tcp",
                    u"port": 8080
                },
                u"paths": {
                    u"/": {
                        "module": u"crossbar.worker.test.test_router",
                        "object": u"hello",
                        "type": u"wsgi"
                    }
                }
            })

        # Make a request to the WSGI app.
        d = treq.get("http://localhost:8080/", reactor=temp_reactor)
        d.addCallback(treq.content)
        d.addCallback(self.assertEqual, b"hello!")
        d.addCallback(lambda _: temp_reactor.stop())

        temp_reactor.run()

        return d


def hello(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/html')])
    log.info('serving response')
    return [b'hello!']
