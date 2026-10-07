"""Bounded observation ("eyes") for UIE."""
from __future__ import annotations
import hashlib, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Mapping

@dataclass(frozen=True)
class Observation:
    observer: str
    target: str
    status: str
    content: str = ""
    content_type: str | None = None
    status_code: int | None = None
    observed_at: str = ""
    duration_ms: int = 0
    content_sha256: str | None = None
    truncated: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

class WebObserver:
    """Read HTTP resources with strict time/size/redirect bounds."""
    def __init__(self, *, timeout_seconds: float=15.0, max_bytes: int=2_000_000,
                 max_redirects: int=3, user_agent: str="UIE-Eyes/0.1"):
        if timeout_seconds<=0 or max_bytes<=0 or max_redirects<0: raise ValueError("invalid observer limits")
        self.timeout_seconds, self.max_bytes, self.max_redirects, self.user_agent = timeout_seconds,max_bytes,max_redirects,user_agent

    def observe(self, url: str) -> Observation:
        if not url.startswith(("http://","https://")): raise ValueError("Only HTTP(S) URLs are supported")
        started=time.monotonic(); stamp=datetime.now(timezone.utc).isoformat()
        req=Request(url,headers={"User-Agent":self.user_agent,"Accept":"text/html,application/json,text/plain,application/xml;q=0.9,*/*;q=0.1"},method="GET")
        try:
            with urlopen(req,timeout=self.timeout_seconds) as r:
                data=r.read(self.max_bytes+1); truncated=len(data)>self.max_bytes
                data=data[:self.max_bytes] if truncated else data
                enc=r.headers.get_content_charset() or "utf-8"; content=data.decode(enc,errors="replace")
                return self._result(url,"observed",content,r.headers.get("Content-Type"),r.status,stamp,started,truncated,{"final_url":r.geturl()})
        except HTTPError as e:
            data=e.read(self.max_bytes+1); truncated=len(data)>self.max_bytes
            data=data[:self.max_bytes] if truncated else data
            content=data.decode("utf-8",errors="replace")
            return self._result(url,"http_error",content,e.headers.get("Content-Type") if e.headers else None,e.code,stamp,started,truncated,{"error":str(e)})
        except (URLError,TimeoutError) as e:
            return self._result(url,"network_error","",None,None,stamp,started,False,{"error":str(e)})

    def _result(self,url,status,content,ctype,code,stamp,started,truncated,metadata):
        return Observation("web",url,status,content,ctype,code,stamp,max(0,int((time.monotonic()-started)*1000)),
                            hashlib.sha256(content.encode()).hexdigest() if content else None,truncated,dict(metadata))
