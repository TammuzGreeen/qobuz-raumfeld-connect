"""Two-process contract test against a simulated speaker; no Qobuz account needed."""
import asyncio
import subprocess
from unittest.mock import MagicMock
from qobuz.client import RaumfeldClient, OwnershipLost
from qobuz.backend import RaumfeldBackend
from qobuz_proxy.backends.types import BackendTrackMetadata


async def run():
    child = subprocess.Popen(['node','tests/fake-node.js'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        port = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=10)
        client=RaumfeldClient('http://127.0.0.1:'+port.strip(),'integration-test-token-12345678901234567890')
        relay=MagicMock()
        relay.register.return_value='http://127.0.0.1:8790/audio/'+'a'*32
        backend=RaumfeldBackend(client,'room','Room',relay)
        await backend.select('integration-selection')
        await backend.stop()  # Initial cloud snapshot must not stop Spotify.
        assert (await client.state())['rooms'][0]['source']=='spotify'
        await backend.play('https://example.test/audio',BackendTrackMetadata('1',title='Test',bit_depth=16,sample_rate=44100))
        await backend.pause()
        await backend.resume()
        await backend.seek(12000)
        await backend.set_volume(40)
        assert (await client.state())['rooms'][0]['owned'] is True
        child.stdin.write('native\n');child.stdin.flush()
        await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=5)
        try:
            await backend.pause()
            raise AssertionError('Native takeover was not protected')
        except OwnershipLost:
            pass
        assert backend.token is None
        assert (await client.state())['rooms'][0]['source']=='spotify'
        print('Python → Node → simulated renderer integration passed')
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill();child.wait()


asyncio.run(run())
