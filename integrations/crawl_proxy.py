"""Small forward proxy: resolve once, reject non-public targets, connect to that IP.

The browser service has only an internal Docker network. This proxy is its sole
HTTP(S) egress; it receives no database, integration or application credentials.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_HEADERS = 65536
MAX_TUNNEL_BYTES = 32 * 1024 * 1024


def public_ip(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and not address.ipv4_mapped.is_global:
            return False
        if address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network('64:ff9b::/96') or address in ipaddress.ip_network('64:ff9b:1::/48'):
            return False
    return True


async def resolve_target(host, port):
    records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM), timeout=5)
    addresses = list(dict.fromkeys(row[4][0] for row in records))
    if not addresses or any(not public_ip(value) for value in addresses):
        raise PermissionError('Non-public destination')
    return [(value, port) for value in addresses]


async def connect_target(host, port):
    targets = await resolve_target(host, port)
    for address, resolved_port in targets[:4]:
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, resolved_port), timeout=5)
        except (OSError, asyncio.TimeoutError):
            continue
    raise OSError('Destination unavailable')


async def copy_stream(reader, writer, budget):
    while True:
        data = await asyncio.wait_for(reader.read(65536), timeout=30)
        if not data:
            return
        budget[0] += len(data)
        if budget[0] > MAX_TUNNEL_BYTES:
            return
        writer.write(data)
        await writer.drain()


async def handle_client(reader, writer):
    upstream = None
    established = False
    try:
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
        if len(raw) > MAX_HEADERS:
            raise ValueError
        lines = raw.decode('iso-8859-1').split('\r\n')
        method, target, protocol = lines[0].split(' ')
        if protocol not in ('HTTP/1.0', 'HTTP/1.1'):
            raise ValueError
        if method == 'CONNECT':
            parts = urlsplit('https://' + target)
            if not parts.hostname or parts.port != 443 or parts.username or parts.password or parts.path:
                raise PermissionError
            upstream_reader, upstream = await connect_target(parts.hostname, 443)
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        else:
            parts = urlsplit(target)
            if (method not in ('GET','HEAD','POST','OPTIONS') or parts.scheme != 'http'
                    or not parts.hostname or parts.port not in (None,80) or parts.username or parts.password):
                raise PermissionError
            headers = []
            names = set()
            for line in lines[1:]:
                if not line:
                    continue
                name, value = line.split(':',1)
                name = name.lower().strip()
                names.add(name)
                if name not in ('host','connection','proxy-connection','proxy-authorization','keep-alive','upgrade'):
                    headers.append(name + ':' + value)
            if {'content-length','transfer-encoding'} <= names:
                raise ValueError
            upstream_reader, upstream = await connect_target(parts.hostname, 80)
            host = parts.hostname.encode('idna').decode()
            authority = '[' + host + ']' if ':' in host else host
            path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            request = '\r\n'.join([f'{method} {path} HTTP/1.1',f'Host: {authority}',
                                    'Connection: close',*headers,'',''])
            upstream.write(request.encode('iso-8859-1'))
            await upstream.drain()
        established = True
        await writer.drain()
        budget = [0]
        copying = [asyncio.create_task(copy_stream(reader, upstream, budget)),
                   asyncio.create_task(copy_stream(upstream_reader, writer, budget))]
        done, pending = await asyncio.wait(copying, timeout=150, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*copying, return_exceptions=True)
    except PermissionError:
        if not established:
            writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
    except Exception:
        if not established:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
    finally:
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()
        if upstream:
            upstream.close()


async def serve(host='0.0.0.0', port=8765):
    semaphore = asyncio.Semaphore(256)
    async def limited(reader, writer):
        async with semaphore:
            await handle_client(reader, writer)
    server = await asyncio.start_server(limited, host, port, limit=MAX_HEADERS)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(serve())
