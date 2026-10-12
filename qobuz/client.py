"""Authenticated Node topology/control client; direct mode uses DLNA separately."""
import aiohttp
import time


class OwnershipLost(RuntimeError):
    pass


class RaumfeldClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def request(self, path, payload=None):
        volume = path == '/v1/control' and payload and payload.get('action') == 'volume'
        diagnostic = {'connection': 'python_to_node', 'endpoint': '/v1/control', 'action': 'volume'} if volume else None
        started = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
                async with session.request("GET" if payload is None else "POST", self.base_url + path,
                                           json=payload, headers={"Authorization": "Bearer " + self.token}) as response:
                    if diagnostic is not None:
                        diagnostic['httpStatus'] = response.status
                    data = await response.json()
                    if response.status != 200:
                        raise OwnershipLost(data.get("error", "node_unavailable"))
                    if data.get("apiVersion") != "1":
                        raise OwnershipLost("unsupported_api")
                    if diagnostic is not None:
                        diagnostic['elapsedMs'] = round((time.monotonic() - started) * 1000)
                        data['controlAPI'] = diagnostic
                    return data
        except Exception as error:
            if diagnostic is not None:
                diagnostic['elapsedMs'] = round((time.monotonic() - started) * 1000)
                diagnostic['exception'] = ('node_rejected' if isinstance(error, OwnershipLost)
                    else 'connection_error' if isinstance(error, aiohttp.ClientConnectionError)
                    else 'request_failed')
                error.control_api_diagnostic = diagnostic
            raise

    async def state(self):
        return await self.request("/v1/state")

    async def control(self, room_id, action, **kwargs):
        return await self.request("/v1/control", {"roomId": room_id, "action": action, **kwargs})

    async def binding(self, room_id, action, **kwargs):
        return await self.request('/v1/binding', {'roomId': room_id, 'action': action, **kwargs})
