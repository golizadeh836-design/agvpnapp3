import json
import threading
import socket
import asyncio
import websockets
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.network.urlrequest import UrlRequest
from kivy.logger import Logger
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.metrics import dp

Window.clearcolor = get_color_from_hex('#121212')

class Socks5Server:
    def __init__(self, worker_host, worker_port, password, local_host='127.0.0.1', local_port=10808):
        self.worker_host = worker_host
        self.worker_port = worker_port
        self.password = password
        self.local_host = local_host
        self.local_port = local_port
        self.server_socket = None
        self.running = False
        self.thread = None

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.local_host, self.local_port))
        self.server_socket.listen(5)
        self.running = True
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()
        Logger.info(f"Socks5Server started on {self.local_host}:{self.local_port}")

    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self.running:
            try:
                client, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(client, loop)).start()
            except:
                break

    def handle_client(self, client_sock, loop):
        try:
            data = client_sock.recv(262)
            if data[0] != 0x05:
                client_sock.close()
                return
            client_sock.send(b'\x05\x00')
            data = client_sock.recv(4)
            if data[1] != 0x01:
                client_sock.close()
                return
            addr_type = data[3]
            if addr_type == 0x01:
                addr = socket.inet_ntoa(client_sock.recv(4))
            elif addr_type == 0x03:
                domain_len = client_sock.recv(1)[0]
                addr = client_sock.recv(domain_len).decode()
            else:
                client_sock.close()
                return
            port = int.from_bytes(client_sock.recv(2), 'big')
            target = f"{addr}:{port}"

            async def connect_worker():
                uri = f"wss://{self.worker_host}/"
                async with websockets.connect(uri) as ws:
                    auth_msg = f"{self.password}|{target}"
                    await ws.send(auth_msg)
                    resp = await ws.recv()
                    if resp != "connected":
                        raise Exception("worker connection failed")
                    response = b'\x05\x00\x00\x01'
                    if addr_type == 0x01:
                        response += socket.inet_aton(addr)
                    else:
                        response += b'\x00\x00\x00\x00'
                    response += port.to_bytes(2, 'big')
                    client_sock.send(response)

                    def forward_to_worker():
                        while True:
                            try:
                                data = client_sock.recv(4096)
                                if not data:
                                    break
                                asyncio.run_coroutine_threadsafe(ws.send(data), loop)
                            except:
                                break
                        asyncio.run_coroutine_threadsafe(ws.close(), loop)

                    async def from_worker():
                        async for msg in ws:
                            if isinstance(msg, bytes):
                                client_sock.send(msg)
                            else:
                                client_sock.send(msg.encode())
                        client_sock.close()

                    t = threading.Thread(target=forward_to_worker)
                    t.daemon = True
                    t.start()
                    await from_worker()

            loop.run_until_complete(connect_worker())
        except Exception as e:
            try:
                client_sock.close()
            except:
                pass

class MainScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = dp(10)
        self.spacing = dp(10)
        self.servers = []
        self.selected_server = None
        self.socks_server = None
        self.is_connected = False

        self.status_label = Label(text='Ag VPN - آماده', size_hint=(1, 0.1))
        self.add_widget(self.status_label)

        self.server_spinner = Spinner(text='انتخاب سرور', values=(), size_hint=(1, 0.1))
        self.server_spinner.bind(text=self.on_server_select)
        self.add_widget(self.server_spinner)

        btn_layout = BoxLayout(size_hint=(1, 0.1), spacing=dp(10))
        self.connect_btn = Button(text='اتصال')
        self.connect_btn.bind(on_press=self.toggle_connection)
        btn_layout.add_widget(self.connect_btn)

        self.refresh_btn = Button(text='به‌روزرسانی')
        self.refresh_btn.bind(on_press=self.refresh_servers)
        btn_layout.add_widget(self.refresh_btn)
        self.add_widget(btn_layout)

        self.refresh_servers()

    def on_server_select(self, spinner, text):
        for server in self.servers:
            if server['name'] == text:
                self.selected_server = server
                self.connect_btn.disabled = False
                break

    def refresh_servers(self, instance=None):
        self.status_label.text = "در حال دریافت لیست سرورها..."
        # آدرس GIST خودت - اینجا دقیقاً آدرسی را که قبلاً فرستادی قرار بده
        url = 'https://gist.githubusercontent.com/golizadeh836-design/9d7e6ebead0591f2bc40667b8ea49916/raw/5dad81c55c9c90c9b4aac63815bd37dbaa9a8fac/servers.json'
        UrlRequest(url, on_success=self.on_servers_loaded, on_failure=self.on_servers_failed)

    def on_servers_loaded(self, req, result):
        try:
            data = json.loads(result)
            self.servers = data
            names = [s['name'] for s in self.servers]
            self.server_spinner.values = names
            self.status_label.text = f"{len(names)} سرور بارگذاری شد"
        except Exception as e:
            self.status_label.text = f"خطا: {e}"

    def on_servers_failed(self, req, result):
        self.status_label.text = "خطا در دریافت لیست"

    def toggle_connection(self, instance):
        if self.is_connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if not self.selected_server:
            self.status_label.text = "لطفاً یک سرور انتخاب کنید"
            return
        server = self.selected_server
        self.status_label.text = f"در حال اتصال به {server['name']}..."
        try:
            self.socks_server = Socks5Server(
                worker_host=server['worker_host'],
                worker_port=server['worker_port'],
                password=server['password']
            )
            self.socks_server.start()
            self.is_connected = True
            self.status_label.text = "متصل شد (SOCKS5 روی 127.0.0.1:10808)"
            self.connect_btn.text = "قطع"
        except Exception as e:
            self.status_label.text = f"خطا: {e}"

    def disconnect(self):
        if self.socks_server:
            self.socks_server.stop()
            self.socks_server = None
        self.is_connected = False
        self.status_label.text = "قطع شد"
        self.connect_btn.text = "اتصال"

class AgVpnApp(App):
    def build(self):
        return MainScreen()

if __name__ == '__main__':
    AgVpnApp().run()