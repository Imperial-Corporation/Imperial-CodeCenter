"""Serveur d'écho TCP (asyncio) et client de test sur le port local."""

import asyncio


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.readline():
        writer.write(data)  # renvoie la ligne reçue
        await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    # Port 0 : le système choisit un port libre.
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"bonjour\n")
    await writer.drain()
    print((await reader.readline()).decode().strip())  # bonjour
    writer.close()
    await writer.wait_closed()

    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
