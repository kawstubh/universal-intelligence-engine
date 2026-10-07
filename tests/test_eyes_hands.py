import threading
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
from universal_intelligence_engine.eyes import WebObserver
from universal_intelligence_engine.hands import HttpHand
from universal_intelligence_engine.runtime import SenseActRuntime

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'{"ok":true}'; self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        self.send_response(204); self.end_headers()
    def log_message(self,*args): pass

@pytest.fixture
def server():
    s=HTTPServer(("127.0.0.1",0),Handler); t=threading.Thread(target=s.serve_forever,daemon=True); t.start()
    yield s; s.shutdown(); t.join(timeout=2)

def test_eye_observes_and_hashes(server):
    r=WebObserver().observe(f"http://127.0.0.1:{server.server_port}/")
    assert r.status=="observed" and r.status_code==200 and r.content=='{"ok":true}' and r.content_sha256

def test_hand_denies_unallowlisted_method(server):
    u=f"http://127.0.0.1:{server.server_port}/"; h=HttpHand(allowed_hosts={"127.0.0.1"})
    with pytest.raises(PermissionError): h.execute(method="POST",url=u,body={"x":1})

def test_hand_executes_allowlisted_action(server):
    u=f"http://127.0.0.1:{server.server_port}/"; h=HttpHand(allowed_hosts={"127.0.0.1"},allowed_methods={"POST"})
    with pytest.raises(PermissionError): h.execute(method="POST",url=u,body={"x":1})
    r=h.execute(method="POST",url=u,body={"x":1},approved=True); assert r.status=="completed" and r.status_code==204

def test_runtime_audits_observation(server):
    u=f"http://127.0.0.1:{server.server_port}/"; rt=SenseActRuntime(); rt.observe_url(u)
    assert rt.audit()[0].capability=="web.observe"


def test_runtime_registers_capabilities(server):
    from universal_intelligence_engine.policy import policy_for
    from universal_intelligence_engine.tools import ToolRegistry
    u=f"http://127.0.0.1:{server.server_port}/"
    rt=SenseActRuntime()
    registry=ToolRegistry(policy_for(allowed=["web.observe"]))
    rt.register_tools(registry)
    result=registry.call("web.observe",url=u)
    assert result.status=="observed"
