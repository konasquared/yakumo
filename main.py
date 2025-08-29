from fastapi import FastAPI, HTTPException
import uuid
import subprocess
import os
import logging
import ipaddress
from typing import Optional

from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator

app = FastAPI()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
        logger.info(f"PortManager initialized with port range {start_port}-{end_port}")
    
    def allocate(self, session_id):
        if not self.available_ports:
            raise HTTPException(status_code=503, detail="No available ports")
        
        port = self.available_ports.pop()
        self.allocated[session_id] = port
        logger.info(f"Allocated port {port} for session {session_id}")
        return port
    
    def release(self, session_id):
        if session_id in self.allocated:
            port = self.allocated.pop(session_id)
            self.available_ports.add(port)
            logger.info(f"Released port {port} for session {session_id}")
        else:
            logger.warning(f"Attempted to release non-existent session {session_id}")

class ProxyManager:
    def __init__(self):
        self.proxies = {}  # session_id -> (ingress_port, target_ip, target_port, chain_name)
        self._ensure_nftables_setup()
    
    def _ensure_nftables_setup(self):
        """Ensure nftables is properly configured with required tables and chains"""
        try:
            # Check if nat table exists, create if it doesn't
            result = subprocess.run(
                ["nft", "list", "table", "ip", "nat"], 
                capture_output=True, 
                text=True, 
                check=False
            )
            
            if result.returncode != 0:
                logger.info("Creating nat table...")
                subprocess.run(["nft", "add", "table", "ip", "nat"], check=True)
            
            # Check if PREROUTING chain exists, create if it doesn't
            result = subprocess.run(
                ["nft", "list", "chain", "ip", "nat", "PREROUTING"], 
                capture_output=True, 
                text=True, 
                check=False
            )
            
            if result.returncode != 0:
                logger.info("Creating PREROUTING chain...")
                subprocess.run([
                    "nft", "add", "chain", "ip", "nat", "PREROUTING", 
                    "{", "type", "nat", "hook", "prerouting", "priority", "-100", ";", "}"
                ], check=True)
            
            # Check if POSTROUTING chain exists, create if it doesn't  
            result = subprocess.run(
                ["nft", "list", "chain", "ip", "nat", "POSTROUTING"], 
                capture_output=True, 
                text=True, 
                check=False
            )
            
            if result.returncode != 0:
                logger.info("Creating POSTROUTING chain...")
                subprocess.run([
                    "nft", "add", "chain", "ip", "nat", "POSTROUTING", 
                    "{", "type", "nat", "hook", "postrouting", "priority", "100", ";", "}"
                ], check=True)
                
            logger.info("nftables setup verified/completed")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to setup nftables: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize nftables")
    
    def _run_nft_command(self, cmd_args, description=""):
        """Run nftables command with proper error handling"""
        try:
            logger.debug(f"Running nft command: {' '.join(cmd_args)}")
            result = subprocess.run(cmd_args, check=True, capture_output=True, text=True)
            return result
        except subprocess.CalledProcessError as e:
            error_msg = f"nftables command failed: {' '.join(cmd_args)}"
            if e.stderr:
                error_msg += f" - Error: {e.stderr.strip()}"
            if description:
                error_msg = f"{description}: {error_msg}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=f"Network configuration failed: {description}")
    
    def _validate_ip_address(self, ip_str):
        """Validate IP address format"""
        try:
            ipaddress.ip_address(ip_str)
            return True
        except ipaddress.AddressValueError:
            return False
    
    def open_proxy(self, session_id, ingress_port, target_ip, target_port):
        """Create a new proxy rule"""
        # Validate inputs
        if not self._validate_ip_address(target_ip):
            raise HTTPException(status_code=400, detail=f"Invalid target IP address: {target_ip}")
        
        if not (1 <= target_port <= 65535):
            raise HTTPException(status_code=400, detail=f"Invalid target port: {target_port}")
        
        if not (1024 <= ingress_port <= 65535):
            raise HTTPException(status_code=400, detail=f"Invalid ingress port: {ingress_port}")
        
        chain_name = f"proxy_{session_id.replace('-', '_')}"
        
        try:
            # Create chain
            self._run_nft_command(
                ["nft", "add", "chain", "ip", "nat", chain_name],
                f"Creating chain {chain_name}"
            )
            
            # Add UDP DNAT rule to the chain
            self._run_nft_command([
                "nft", "add", "rule", "ip", "nat", chain_name,
                "udp", "dport", str(ingress_port),
                "dnat", "to", f"{target_ip}:{target_port}"
            ], f"Adding UDP DNAT rule for {target_ip}:{target_port}")

            # Add TCP DNAT rule to the chain
            self._run_nft_command([
                "nft", "add", "rule", "ip", "nat", chain_name,
                "tcp", "dport", str(ingress_port),
                "dnat", "to", f"{target_ip}:{target_port}"
            ], f"Adding TCP DNAT rule for {target_ip}:{target_port}")
            
            # Jump to our chain from PREROUTING
            self._run_nft_command([
                "nft", "add", "rule", "ip", "nat", "PREROUTING",
                "jump", chain_name
            ], f"Adding jump rule to {chain_name}")
            
            # Add UDP SNAT rule for return traffic
            self._run_nft_command([
                "nft", "add", "rule", "ip", "nat", "POSTROUTING",
                "ip", "daddr", target_ip, "udp", "dport", str(target_port),
                "masquerade"
            ], f"Adding UDP SNAT rule for return traffic")

            # Add TCP SNAT rule for return traffic
            self._run_nft_command([
                "nft", "add", "rule", "ip", "nat", "POSTROUTING",
                "ip", "daddr", target_ip, "tcp", "dport", str(target_port),
                "masquerade"
            ], f"Adding TCP SNAT rule for return traffic")
            
            self.proxies[session_id] = (ingress_port, target_ip, target_port, chain_name)
            logger.info(f"Created proxy: {ingress_port} -> {target_ip}:{target_port} (session: {session_id})")
            return session_id
            
        except Exception as e:
            # Cleanup on failure
            logger.error(f"Failed to create proxy for session {session_id}: {e}")
            self._cleanup_proxy_rules(session_id, chain_name, target_ip, target_port)
            raise

    def _cleanup_proxy_rules(self, session_id, chain_name, target_ip=None, target_port=None):
        """Clean up proxy rules, ignoring errors"""
        try:
            # Remove POSTROUTING rule if we have target info
            if target_ip and target_port:
                subprocess.run([
                    "nft", "delete", "rule", "ip", "nat", "POSTROUTING",
                    "ip", "daddr", target_ip, "udp", "dport", str(target_port),
                    "masquerade"
                ], check=False, capture_output=True)
            
            # Flush and delete chain
            subprocess.run(["nft", "flush", "chain", "ip", "nat", chain_name], 
                          check=False, capture_output=True)
            subprocess.run(["nft", "delete", "chain", "ip", "nat", chain_name], 
                          check=False, capture_output=True)
            
        except Exception as e:
            logger.warning(f"Error during cleanup for session {session_id}: {e}")

    def close_proxy(self, session_id):
        """Remove a proxy rule"""
        if session_id not in self.proxies:
            logger.warning(f"Attempted to close non-existent proxy session: {session_id}")
            return
        
        ingress_port, target_ip, target_port, chain_name = self.proxies.pop(session_id)
        
        try:
            # Remove POSTROUTING masquerade rule
            self._run_nft_command([
                "nft", "delete", "rule", "ip", "nat", "POSTROUTING",
                "ip", "daddr", target_ip, "udp", "dport", str(target_port),
                "masquerade"
            ], f"Removing SNAT rule for {target_ip}:{target_port}")
            
            # Flush and delete chain
            self._run_nft_command(
                ["nft", "flush", "chain", "ip", "nat", chain_name],
                f"Flushing chain {chain_name}"
            )
            self._run_nft_command(
                ["nft", "delete", "chain", "ip", "nat", chain_name],
                f"Deleting chain {chain_name}"
            )
            
            logger.info(f"Closed proxy: {ingress_port} -> {target_ip}:{target_port} (session: {session_id})")
            
        except Exception as e:
            logger.error(f"Error closing proxy for session {session_id}: {e}")
            # Still remove from our tracking even if nftables cleanup failed
            
    def list_proxies(self):
        """List all active proxies"""
        return {
            session_id: {
                "ingress_port": ingress_port,
                "target_ip": target_ip, 
                "target_port": target_port
            }
            for session_id, (ingress_port, target_ip, target_port, _) in self.proxies.items()
        }

@app.middleware("http")
async def verify_token(request, call_next):
    # Skip auth for health check
    if request.url.path == "/health":
        response = await call_next(request)
        return response
        
    access_token = os.getenv("ACCESS_TOKEN")
    if access_token:
        token = request.headers.get("Authorization")
        if token != f"Bearer {access_token}":
            logger.warning(f"Unauthorized access attempt from {request.client.host if request.client else 'unknown'}")
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    response = await call_next(request)
    return response

@app.get("/")
async def read_root():
    return {"message": "Hello from the Yakumo Routing service!"}

@app.get("/health")
async def health_check():
    try:
        # Test nftables access
        subprocess.run(["nft", "list", "tables"], check=True, capture_output=True)
        nft_status = "ok"
    except Exception as e:
        logger.error(f"nftables health check failed: {e}")
        nft_status = "error"
    
    return {
        "status": "healthy",
        "nftables": nft_status,
        "active_proxies": len(proxy_manager.proxies),
        "available_ports": len(port_manager.available_ports)
    }

port_manager = PortManager()
proxy_manager = ProxyManager()

class ProxyRequest(BaseModel):
    target_ip: str
    target_port: int
    
    @validator('target_ip')
    def validate_ip(cls, v):
        try:
            ipaddress.ip_address(v)
            return v
        except ipaddress.AddressValueError:
            raise ValueError('Invalid IP address format')
    
    @validator('target_port')
    def validate_port(cls, v):
        if not (1 <= v <= 65535):
            raise ValueError('Port must be between 1 and 65535')
        return v

class SessionRequest(BaseModel):
    session_id: str
    
    @validator('session_id')
    def validate_session_id(cls, v):
        try:
            uuid.UUID(v)
            return v
        except ValueError:
            raise ValueError('Invalid session ID format')

@app.post("/open_proxy")
async def open_proxy(request: ProxyRequest):
    try:
        session_id = str(uuid.uuid4())
        ingress_port = port_manager.allocate(session_id)
        proxy_manager.open_proxy(session_id, ingress_port, request.target_ip, request.target_port)

        logger.info(f"Opened proxy {session_id}: {ingress_port} -> {request.target_ip}:{request.target_port}")
        return {
            "session_id": session_id,
            "ingress_port": ingress_port,
            "target_ip": request.target_ip,
            "target_port": request.target_port
        }
    except Exception as e:
        logger.error(f"Failed to open proxy: {e}")
        raise

@app.post("/close_proxy")
async def close_proxy(request: SessionRequest):
    try:
        if request.session_id not in proxy_manager.proxies:
            raise HTTPException(status_code=404, detail="Session not found")
            
        proxy_manager.close_proxy(request.session_id)
        port_manager.release(request.session_id)

        logger.info(f"Closed proxy session {request.session_id}")
        return {"status": "closed", "session_id": request.session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to close proxy: {e}")
        raise HTTPException(status_code=500, detail="Failed to close proxy")

@app.get("/list_proxies")
async def list_proxies():
    """List all active proxy sessions"""
    return {
        "active_proxies": proxy_manager.list_proxies(),
        "total_count": len(proxy_manager.proxies)
    }
