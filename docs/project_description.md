# WebTunnel

Данный проект представляет собой Python библиотеку для туннелирования.

Данный проект может использоваться как на локальной машине, сервере или на Kaggle платформе.

## Установка

```bash
pip install git+https://github.com/dexforint/WebTunnel.git
```

## Использование

```python
from webtunnel import ZROK

tunnel = ZROK(token="...")
tunnel.install()
tunnel.start()
```

# Твоя задача

Помоги мне разработать данный проект.

Мне так же интересны твои мысли, замечания, советы.

- используй pyproject.toml (uv)
- Если у тебя будут вопросы - не стесняйся задавать их.
- Проект в целом и сам твой код должны соответствовать нормам профессиональной разработки.
- По возможности твой код не должен получать предупреждения от Pylance.
- При написании кода для данного проекта AICommentProject пиши понятные исчерпывающие комментарии на русском языке.

За основу используй следующий код:

```python
# %%
!pip install fastapi uvicorn > /dev/null

import uvicorn
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  #  Замените на адрес сервиса анализа, если знаете
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/test")
async def test():
    return {
        "status": "ok"
    }

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)

server_thread = threading.Thread(target=run_server)
server_thread.start()

# %%
import os
import subprocess
import threading
import time
import socket
import urllib

def run_command(cmd, printout=False):
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, bufsize=1)
    stdout, stderr = p.communicate()
    if printout:
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)
    return p

# %% [markdown]
# # Tunnel

# %%
from abc import ABC, abstractmethod
import threading

class Tunnel(ABC):
    def __init__(self, token: str | None =None):
        self.token = token

    @abstractmethod
    def install(self):
        pass

    @abstractmethod
    def thread(self, port=8000):
        pass

    def start(self):
        threading.Thread(target=self.thread, daemon=True).start()

# %% [markdown]
# ## Zrok

# %%
class ZROK(Tunnel):
    name = "zrok"

    def install(self):
        # os.chdir('/tmp')
        get_ipython().system('wget -q https://github.com/openziti/zrok/releases/download/v2.0.0-rc7/zrok_2.0.0-rc7_linux_amd64.tar.gz')
        get_ipython().system('tar -xvzf zrok_2.0.0-rc7_linux_amd64.tar.gz')
        get_ipython().system('chmod +x zrok2')
        print("🔐 Enabling zrok environment...")
        cmd = f"./zrok2 enable {self.token}"
        p = run_command(cmd)
        if p.returncode == 0:
            print("✅ zrok enabled successfully!")
        else:
            if 'you already have an enabled environment' in p.stderr:
                print("✅ zrok is already enabled!")
            else:
                print(f"❌ Error enabling zrok: {p.stderr}")

    def thread(self, port=8000):
        cmd = f"./zrok2 share public localhost:{port} --headless"
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
        print('====== zrok enable =====')
        for line in p.stdout:
            print('[ZROK]:', line.strip())
            if 'shares.zrok.io' in line:
                import re

                m = re.search(r'(?im)\b([a-z0-9-]+\.shares\.zrok\.io)\b', line)
                url = m.group(1) if m else None

                msg = f'Zrok URL: {url}'
                border = '        +' + '-' * (len(msg) + 2) + '+'
                print()
                print(border)
                print(f".       | {msg} |")
                print(border)
                print()
            if '[ERROR]' in line:
                msg = 'Zrok Error: Cannot create public link. Visit https://api.zrok.io and delete some environments.'
                border = '        +' + '-' * (len(msg) + 2) + '+'
                print(border)
                print(f".       | {msg} |")
                print(border)

# %% [markdown]
# ## Ngrok

# %%
class NGROK(Tunnel):
    name = "ngrok"

    def install(self):
        get_ipython().system('pip install -q pyngrok')
        print("pyngrok installed!")

    def thread(self, port=8000):
        print("\n Attention! Ngrok requires VPN in Russia!")
        from pyngrok import ngrok
        ngrok.set_auth_token(self.token)
        http_tunnel = ngrok.connect(port)
        print(f'NGROK URL: {http_tunnel.public_url}')

# %% [markdown]
# ## localtunnel

# %%
class LocalTunnel(Tunnel):
    name = "localtunnel"

    def install(self):
        get_ipython().system('npm install -g localtunnel')
        print("localtunnel installed!")

    def thread(self, port=8000):
        print("Tunnel Password:", urllib.request.urlopen('https://ipv4.icanhazip.com').read().decode('utf8').strip("\n"))
        p = subprocess.Popen(["lt", "--port", str(port)], stdout=subprocess.PIPE)
        for line in p.stdout:
            print(line.decode(), end='')

# %% [markdown]
# ## cloudflared

# %%
class Cloudflared(Tunnel):
    name = "cloudflared"

    def install(self):
        get_ipython().system('wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb')
        get_ipython().system('dpkg -i cloudflared-linux-amd64.deb')
        print("cloudflared installed!")

    def thread(self, port=8000):
        get_ipython().system(f'cloudflared tunnel --url http://localhost:{port}')

# %% [markdown]
# ## pinggy

# %%
class Pinggy(Tunnel):
    name = "pinggy"

    def install(self):
        pass

    def thread(self, port=8000):
        print("\n🌐 Starting Pinggy tunnel...")
        print("\n Caution! Pinggy requires VPN in Russia!")
        print("⏳ Waiting for Pinggy URL...\n")

        # Создаем SSH конфиг
        ssh_config = """
    Host pinggy
        HostName a.pinggy.io
        Port 443
        StrictHostKeyChecking no
        UserKnownHostsFile /dev/null
        ServerAliveInterval 30
        """

        get_ipython().system('mkdir -p ~/.ssh')
        with open(os.path.expanduser('~/.ssh/config'), 'w') as f:
            f.write(ssh_config)

        # Запуск pinggy
        cmd = f'ssh -R0:localhost:{port} pinggy'

        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)

        url_found = False
        for line in p.stdout:
            print('[PINGGY]:', line.strip())

            if not url_found and ('http://' in line or 'https://' in line):
                import re
                # Ищем URL формата http://xxxxx.a.pinggy.link
                urls = re.findall(r'https?://[a-zA-Z0-9\-\.]+\.pinggy\.[a-z]+', line)
                if urls:
                    url = urls[0]
                    msg = f'Pinggy URL: {url}'
                    border = '        +' + '-' * (len(msg) + 2) + '+'
                    print()
                    print(border)
                    print(f".       | {msg} |")
                    print(border)
                    print()
                    url_found = True

# %% [markdown]
# # Test

# %%
tunnel_builders: dict[str, Tunnel] = {
    "zrok": (ZROK, "dGi5n5cyC8q7"),
    "ngrok": (NGROK, "2a39vbvEM8fO1aj5h7p5VuibQvK_3SACZDUBhgmWb9cJcUqmA"),
    "localtunnel": (LocalTunnel, None),
    "cloudflared": (Cloudflared, None),
    "pinggy": (Pinggy, None),
}

# %%
CONNECTION = "pinggy"

print("CONNECTION:", CONNECTION)

tunnel_builder, token = tunnel_builders[CONNECTION]
tunnel = tunnel_builder(token=token)

tunnel.install()
tunnel.start()
```
