"""Pinned stack with bounded HTTP/GENA compatibility and explicit lifecycle."""
from datetime import datetime
from functools import partial
import ipaddress
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, ClientError, web
from defusedxml.ElementTree import fromstring
from async_upnp_client.const import HttpResponse
from async_upnp_client.client import UpnpRequester
from async_upnp_client.exceptions import UpnpActionError, UpnpValueError
from async_upnp_client.server import (
    UpnpServer, EventSubscriber,
    _parse_action_body, _create_action_response, _create_error_action_response, to_xml,
)


class Requester(UpnpRequester):
    def __init__(self, session):
        self.session = session

    async def async_http_request(self, request):
        try:
            async with self.session.request(request.method, request.url,
                    headers=request.headers, data=request.body, allow_redirects=False) as response:
                return HttpResponse(response.status, dict(response.headers), '')
        except (ClientError, TimeoutError):
            return HttpResponse(503, {}, '')


async def soap(service, request):
    try:
        header = request.headers.get('SOAPAction', '').strip('"')
        service_type, action = header.split('#')
        if service_type != service.service_type or action not in service.actions:
            raise UpnpActionError(error_code=401)
        root = fromstring(await request.read())
        body = root.find('{http://schemas.xmlsoap.org/soap/envelope/}Body')
        if body is None or len(body) != 1 or body[0].tag != '{' + service_type + '}' + action:
            raise UpnpActionError(error_code=402)
        tags = [arg.tag for arg in body[0]]
        expected = [arg.name for arg in service.action(action).in_arguments()]
        if sorted(tags) != sorted(expected):
            raise UpnpActionError(error_code=402)
        action, arguments = await _parse_action_body(service, request)
        result = await service.async_handle_action(action, **arguments)
        return _create_action_response(service, action, result)
    except (UpnpActionError, UpnpValueError) as error:
        return _create_error_action_response(error)
    except Exception:
        return _create_error_action_response(UpnpActionError(error_code=402))


async def subscribe(service, request):
    # Narrow fixes over 0.49.0: uuid-prefixed SID; Second-N timeout; strict,
    # bounded callbacks; expiry-aware renewal; initial response before NOTIFY.
    service._subscribers[:] = [s for s in service._subscribers if s.expiration > datetime.now()]
    sid = request.headers.get('SID')
    callback = request.headers.get('CALLBACK')
    timeout = request.headers.get('TIMEOUT', 'Second-1800')
    try:
        if timeout.lower() == 'second-infinite':
            timeout = 1800
        elif timeout.lower().startswith('second-'):
            timeout = max(30, min(1800, int(timeout[7:])))
        else:
            raise ValueError()
    except ValueError:
        return web.Response(status=412)
    if sid:
        if callback or request.headers.get('NT'):
            return web.Response(status=412)
        subscriber = service.get_subscriber(sid)
        if subscriber is None:
            return web.Response(status=412)
        subscriber.timeout = timeout
    else:
        if request.headers.get('NT') != 'upnp:event' or not callback or len(service._subscribers) >= 32:
            return web.Response(status=412)
        try:
            if not callback.startswith('<') or not callback.endswith('>') or len(callback) > 2048:
                raise ValueError()
            url = callback[1:-1]
            parsed = urlsplit(url)
            if (parsed.scheme != 'http' or parsed.username or parsed.password or parsed.fragment
                    or ipaddress.ip_address(parsed.hostname) != ipaddress.ip_address(request.remote)):
                raise ValueError()
        except (ValueError, TypeError):
            return web.Response(status=412)
        subscriber = EventSubscriber(url, timeout)
        subscriber._uuid = 'uuid:' + subscriber.uuid
    response = web.Response(headers={'SID': subscriber.uuid, 'TIMEOUT': f'Second-{timeout}'})
    if not sid:
        await response.prepare(request)
        await response.write_eof()
        service.add_subscriber(subscriber)
        await service.async_send_events(subscriber)
    return response


async def unsubscribe(service, request):
    if request.headers.get('CALLBACK') or request.headers.get('NT'):
        return web.Response(status=412)
    return web.Response(status=200 if service.del_subscriber(request.headers.get('SID', '')) else 412)


class RendererServer(UpnpServer):
    """One root MediaRenderer per room; SSDP uses the stack unchanged."""
    async def start(self, ssdp=True):
        self.session = ClientSession(timeout=ClientTimeout(total=5))
        self.base_uri = f'http://{self.source[0]}:{self.http_port}'
        self._device = self.server_device(Requester(self.session), self.base_uri, self.boot_id, self.config_id)
        self.app = web.Application(client_max_size=96 * 1024)
        self.app.router.add_get(self._device.device_url, partial(to_xml, self._device))
        for service in self._device.all_services:
            definition = service.SERVICE_DEFINITION
            self.app.router.add_get(definition.scpd_url, partial(to_xml, service))
            self.app.router.add_post(definition.control_url, partial(soap, service))
            self.app.router.add_route('SUBSCRIBE', definition.event_sub_url, partial(subscribe, service))
            self.app.router.add_route('UNSUBSCRIBE', definition.event_sub_url, partial(unsubscribe, service))
        async def status(request):
            room = self._device.room
            # Local operator status only: no room IDs, URLs or metadata in JSON.
            if request.remote not in {'127.0.0.1', self.source[0]}:
                raise web.HTTPForbidden()
            return web.json_response({'transport': room.transport, 'status': room.status,
                'limitation': room.limitation, 'pendingSeek': room.pending_seek is not None})
        self.app.router.add_get('/bridge-status', status)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        self._site = web.TCPSite(self.runner, self.source[0], self.http_port)
        await self._site.start()
        self.ssdp_started = ssdp
        if ssdp:
            await self._async_start_ssdp()

    async def stop(self):
        if getattr(self, '_advertisement_announcer', None) and self._advertisement_announcer._transport:
            await self._advertisement_announcer.async_stop()
        if getattr(self, '_search_responder', None) and self._search_responder._transport:
            responder = self._search_responder
            await responder.async_stop()
            # 0.49.0 closes only the listening socket, not its response socket.
            if responder._response_transport:
                responder._response_transport.close()
        if hasattr(self, 'runner'):
            await self.runner.cleanup()
        if hasattr(self, 'session'):
            await self.session.close()
