import asyncio
import json
import os
import uuid
from aiohttp import web, WSCloseCode
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
HOST = '192.168.178.161'
PORT = 8081
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

# Store connected clients and admin
clients = {}  # client_id -> {'ws': websocket, 'info': client_info}
admin_ws = None  # Global admin WebSocket

async def index(request):
    return web.FileResponse(os.path.join(STATIC_DIR, 'index.html'))

async def broadcast_to_admin(message):
    global admin_ws
    if admin_ws and not admin_ws.closed:
        try:
            await admin_ws.send_json(message)
        except Exception as e:
            logger.error(f"Error broadcasting to admin: {e}")
            admin_ws = None

async def websocket_handler(request):
    global admin_ws
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    try:
        # Register new client or admin
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    logger.info(f"Received message: {data}")  # Add logging
                    
                    if data['type'] == 'register_client':
                        client_id = data.get('client_id') or str(uuid.uuid4())
                        clients[client_id] = {
                            'ws': ws,
                            'info': {
                                'name': data.get('name', 'Unknown Device'),
                                'hostname': data.get('info', {}).get('hostname', ''),
                                'ip': data.get('info', {}).get('ip', ''),
                                'platform': data.get('info', {}).get('platform', ''),
                                'python_version': data.get('info', {}).get('python_version', '')
                            }
                        }
                        # Notify admin
                        await broadcast_to_admin({
                            'type': 'clients',
                            'clients': [{'client_id': cid, **info['info']} for cid, info in clients.items()]
                        })
                        await ws.send_json({'type': 'registered', 'client_id': client_id})
                    elif data['type'] == 'admin_connect':
                        admin_ws = ws
                        # Send current clients
                        await ws.send_json({
                            'type': 'clients',
                            'clients': [{'client_id': cid, **info['info']} for cid, info in clients.items()]
                        })
                    elif data['type'] == 'command':
                        # Forward command to target client
                        client_id = data.get('client_id')
                        if client_id in clients and not clients[client_id]['ws'].closed:
                            logger.info(f"Forwarding command to client {client_id}: {data}")  # Add logging
                            await clients[client_id]['ws'].send_json(data)
                        else:
                            logger.error(f"Client {client_id} not found or disconnected")
                            if admin_ws and not admin_ws.closed:
                                await admin_ws.send_json({
                                    'type': 'response',
                                    'client_id': client_id,
                                    'action': 'error',
                                    'data': 'Client not found or disconnected'
                                })
                    elif data['type'] == 'response':
                        # Forward responses back to admin
                        await broadcast_to_admin(data)
                    else:
                        logger.warning(f"Unknown message type: {data['type']}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON received: {msg.data}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    logger.exception(e)  # Add full traceback
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f'WebSocket connection closed with exception {ws.exception()}')
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED):
                break
    finally:
        # Cleanup on disconnect
        if ws is admin_ws:
            admin_ws = None
        # Remove from clients if it was a client
        for cid, info in list(clients.items()):
            if info['ws'] is ws:
                del clients[cid]
                await broadcast_to_admin({
                    'type': 'clients',
                    'clients': [{'client_id': cid, **info['info']} for cid, info in clients.items()]
                })
    return ws

async def on_shutdown(app):
    # Close all client connections
    for client in clients.values():
        await client['ws'].close(code=WSCloseCode.GOING_AWAY, message='Server shutdown')
    # Close admin connection
    if admin_ws:
        await admin_ws.close(code=WSCloseCode.GOING_AWAY, message='Server shutdown')

def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/static', STATIC_DIR)
    
    # Add shutdown handler
    app.on_shutdown.append(on_shutdown)
    
    # Run the server
    web.run_app(app, host=HOST, port=PORT, ssl_context=None)

if __name__ == '__main__':
    main() 