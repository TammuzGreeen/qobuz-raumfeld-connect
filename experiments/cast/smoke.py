"""Offline TLS/framing/auth diagnostics, NOT an Android Cast SDK acceptance test."""
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint((number << 3) | 2) + varint(len(value)) + value


def fields(data):
    position, result = 0, {}
    def integer():
        nonlocal position
        number = shift = 0
        while True:
            part = data[position]; position += 1
            number |= (part & 127) << shift
            if part < 128:
                return number
            shift += 7
    while position < len(data):
        tag = integer()
        if tag & 7 == 0:
            value = integer()
        else:
            assert tag & 7 == 2
            size = integer()
            value = data[position:position+size]; position += size
        result[tag >> 3] = value
    return result


def exact(stream, size):
    data = b''
    while len(data) < size:
        part = stream.recv(size-len(data))
        assert part
        data += part
    return data


def exchange(stream, namespace, payload, binary=False):
    frame = (field(1,0)+field(2,'synthetic-sender')+field(3,'receiver-0')+field(4,namespace)
        +field(5,int(binary))+field(7 if binary else 6, payload if binary else json.dumps(payload)))
    stream.sendall(struct.pack('!I',len(frame))+frame)
    response = fields(exact(stream, struct.unpack('!I',exact(stream,4))[0]))
    return response[7] if binary else json.loads(response[6])


def main():
    root = Path(__file__).parent
    env = dict(os.environ, CAST_EXPERIMENT='0')
    assert subprocess.check_output(['/usr/local/bin/cast-diagnostic'],env=env).strip()==b'cast_experiment_disabled'
    assert subprocess.check_output([os.sys.executable,str(root/'service.py')],env=env).strip()==b'cast_experiment_disabled'
    with tempfile.TemporaryDirectory() as directory:
        data = Path(directory)
        config = json.loads((root/'config.example.json').read_text())
        (data/'config.json').write_text(json.dumps(config))
        token = 's'*32
        env.update(CAST_EXPERIMENT='1', CAST_BIND_ADDRESS='127.0.0.1', CAST_DATA_DIR=directory,
                   CAST_ADVERTISE='0', CAST_OBSERVE_ROOMS='0', CAST_API_TOKEN=token)
        process = subprocess.Popen([os.sys.executable,str(root/'service.py')],env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+40
            while not (data/'wire-status.json').exists():
                assert process.poll() is None and time.monotonic()<deadline
                time.sleep(.1)
            public_der=(data/'diagnostic-tls.der').read_bytes()
            # Fixture pins its own generated PUBLIC certificate. TLS chain and
            # hostname verification stay enabled; no Android/Google trust altered.
            context=ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert(public_der))
            assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
            with socket.create_connection(('127.0.0.1',8809),timeout=5) as tcp:
                with context.wrap_socket(tcp,server_hostname='OpenChromecast') as stream:
                    namespace='urn:x-cast:com.google.cast.'
                    nonce=b'synthetic-nonce'
                    response=exchange(stream,namespace+'tp.deviceauth',
                        field(1,field(1,1)+field(2,nonce)+field(3,1)),True)
                    auth=fields(fields(response)[2])
                    assert auth[5]==nonce and auth[2]==public_der
                    result=exchange(stream,namespace+'receiver',{'type':'GET_APP_AVAILABILITY',
                        'requestId':1,'appId':['FD1F76C5','CC1AD845']})
                    assert set(result['availability'].values())=={'APP_UNAVAILABLE'}
                    result=exchange(stream,namespace+'receiver',{'type':'LAUNCH','requestId':2,'appId':'FD1F76C5'})
                    assert result['type']=='LAUNCH_ERROR'
                    result=exchange(stream,namespace+'media',{'type':'LOAD','requestId':3,'currentTime':7200,
                        'media':{'contentId':'https://audio.example.test/DO_NOT_LOG?token=synthetic'}})
                    assert result['type']=='INVALID_REQUEST' and 'status' not in result
            def api(path, body=None):
                request=urllib.request.Request('http://127.0.0.1:8799/api/'+path,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(request,timeout=5) as response:
                        return response.status,json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code,json.load(error)
            status,state=api('status')
            assert status==200 and state['wire']['senderAcceptance']=='unverified'
            assert state['wire']['counters']['authResponseSentNotAccepted']==1
            assert state['wire']['counters']['applicationLaunchRejected']==1
            assert state['wire']['counters']['mediaRejected']==1
            assert state['grouping']=='unknown' and not state['mediaEnabled']
            assert api('select',{'roomId':'synthetic-room-b'})[0]==200
            status,error=api('group',{'enabled':True})
            assert status==409 and not error['mutationSent']
            assert api('snapshot',{})[0]==409
            assert json.loads((data/'config.json').read_text())==config
            assert (data/'gui-token').stat().st_mode & 0o777==0o600
        finally:
            process.terminate()
            stdout,stderr=process.communicate(timeout=15)
        assert process.returncode==0,(stdout,stderr)
        assert b'DO_NOT_LOG' not in stdout+stderr
        assert b'phase=soap_sending' not in stdout+stderr
        assert b'phase=library_call' not in stdout+stderr
        print('Offline diagnostic TLS/framing/auth exchange, explicit app/media rejection, GUI, storage and shutdown passed; Android SDK acceptance remains untested.')


if __name__=='__main__':
    main()
