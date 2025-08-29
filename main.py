from fastapi import FastAPI
import uuid
import subprocess
import os

from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

def mini_load_dotenv(dotenv_path):
    if not dotenv_path:
        dotenv_path = '.env'
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path) as f:
        for line in f:
            if line.startswith('#') or '=' not in line:
                continue
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

mini_load_dotenv('.env')

class PortManager:
    def __init__(self, start_port=10000, end_port=20000):
        self.available_ports = set(range(start_port, end_port))
        self.allocated = {}  # session_id -> port
    
    def allocate(self, session_id):
        port = self.available_ports.pop()
        self.allocated[session_id] = port
        return port
    
    def release(self, session_id):
        if session_id in self.allocated:
            port = self.allocated.pop(session_id)
            self.available_ports.add(port)

class ProxyManager:
    def __init__(self):
        self.proxies = {}  # session_id -> (ingress_port, target_ip, target_port)
    
    def open_proxy(self, session_id, ingress_port, target_ip, target_port):
        chain_name = f"proxy_{session_id.replace('-', '_')}"
        
        # Create chain and add rule
        subprocess.run(["nft", "add", "chain", "ip", "nat", chain_name], check=True)
        subprocess.run([
            "nft", "add", "rule", "ip", "nat", chain_name,
            "udp", "dport", str(ingress_port),
            "dnat", "to", f"{target_ip}:{target_port}"
        ], check=True)
        
        # Jump to our chain from PREROUTING
        subprocess.run([
            "nft", "add", "rule", "ip", "nat", "PREROUTING",
            "jump", chain_name
        ], check=True)
        
        self.proxies[session_id] = (ingress_port, target_ip, target_port, chain_name)
        return session_id

    def close_proxy(self, session_id):
        if session_id in self.proxies:
            _, _, _, chain_name = self.proxies.pop(session_id)
            subprocess.run(["nft", "flush", "chain", "ip", "nat", chain_name], check=True)
            subprocess.run(["nft", "delete", "chain", "ip", "nat", chain_name], check=True)

@app.middleware("http")
async def verify_token(request, call_next):
    access_token = os.getenv("ACCESS_TOKEN")
    if access_token:
        token = request.headers.get("Authorization")
        if token != f"Bearer {access_token}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    response = await call_next(request)
    return response

@app.get("/")
async def read_root():
    return {"message": "Hello from the Yakumo Routing service!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

port_manager = PortManager()
proxy_manager = ProxyManager()

@app.get("/open_proxy")
async def open_proxy(target_ip: str, target_port: int):
    session_id = str(uuid.uuid4())
    ingress_port = port_manager.allocate(session_id)
    proxy_manager.open_proxy(session_id, ingress_port, target_ip, target_port)

    return {
        "session_id": session_id,
        "ingress_port": ingress_port,
        "target_ip": target_ip,
        "target_port": target_port
    }

@app.get("/close_proxy")
async def close_proxy(session_id: str):
    proxy_manager.close_proxy(session_id)
    port_manager.release(session_id)

    return {"status": "closed", "session_id": session_id}
