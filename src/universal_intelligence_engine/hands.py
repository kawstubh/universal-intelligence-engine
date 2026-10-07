"""Governed HTTP execution ("hands") for UIE."""
from __future__ import annotations
import json,time
from dataclasses import dataclass,field
from typing import Mapping
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

@dataclass(frozen=True)
class ActionResult:
    executor:str
    target:str
    method:str
    status:str
    status_code:int|None=None
    response:str=""
    duration_ms:int=0
    attempts:int=1
    metadata:Mapping[str,object]=field(default_factory=dict)

class HttpHand:
    """Perform only explicitly allowlisted HTTP actions; mutations are never implicit."""
    SAFE_METHODS=frozenset({"GET","HEAD","OPTIONS"})
    IDEMPOTENT_METHODS=frozenset({"GET","HEAD","OPTIONS","PUT","DELETE"})
    def __init__(self,*,allowed_hosts:set[str]|frozenset[str]=frozenset(),
                 allowed_methods:set[str]|frozenset[str]=frozenset({"GET"}),
                 timeout_seconds:float=15.0,max_response_bytes:int=2_000_000,
                 max_request_bytes:int=1_000_000,max_retries:int=2):
        self.allowed_hosts=frozenset(h.lower() for h in allowed_hosts)
        self.allowed_methods=frozenset(m.upper() for m in allowed_methods)
        self.timeout_seconds=timeout_seconds; self.max_response_bytes=max_response_bytes
        self.max_request_bytes=max_request_bytes; self.max_retries=max(0,min(max_retries,5))

    def execute(self,*,method:str,url:str,body:object|None=None,headers:Mapping[str,str]|None=None)->ActionResult:
        method=method.upper()
        if method not in self.allowed_methods: raise PermissionError(f"HTTP method not allowed: {method}")
        if not url.startswith(("http://","https://")): raise ValueError("Only HTTP(S) URLs are supported")
        from urllib.parse import urlparse
        host=(urlparse(url).hostname or "").lower()
        if not host or host not in self.allowed_hosts: raise PermissionError(f"HTTP host not allowlisted: {host or '<missing>'}")
        payload=self._encode(body)
        if len(payload)>self.max_request_bytes: raise ValueError("HTTP request body exceeds configured limit")
        hdr={"User-Agent":"UIE-Hands/0.1",**dict(headers or {})}
        if payload and "Content-Type" not in hdr: hdr["Content-Type"]="application/json"
        attempts=0; started=time.monotonic(); last_error=""
        max_attempts=1+(self.max_retries if method in self.IDEMPOTENT_METHODS else 0)
        while attempts<max_attempts:
            attempts+=1
            try:
                with urlopen(Request(url,data=payload or None,headers=hdr,method=method),timeout=self.timeout_seconds) as r:
                    data=r.read(self.max_response_bytes+1); trunc=len(data)>self.max_response_bytes
                    data=data[:self.max_response_bytes] if trunc else data
                    return ActionResult("http",url,method,"completed",r.status,data.decode(r.headers.get_content_charset() or "utf-8",errors="replace"),
                        max(0,int((time.monotonic()-started)*1000)),attempts,{"truncated":trunc})
            except HTTPError as e:
                last_error=str(e)
                if method not in self.IDEMPOTENT_METHODS or attempts>=max_attempts:
                    return ActionResult("http",url,method,"http_error",e.code,duration_ms=max(0,int((time.monotonic()-started)*1000)),attempts=attempts,metadata={"error":last_error})
            except (URLError,TimeoutError) as e:
                last_error=str(e)
                if attempts>=max_attempts:
                    return ActionResult("http",url,method,"network_error",duration_ms=max(0,int((time.monotonic()-started)*1000)),attempts=attempts,metadata={"error":last_error})
            time.sleep(min(.5*(2**(attempts-1)),2.0))
        return ActionResult("http",url,method,"failed",duration_ms=max(0,int((time.monotonic()-started)*1000)),attempts=attempts,metadata={"error":last_error})

    @staticmethod
    def _encode(body):
        if body is None:return b""
        if isinstance(body,bytes):return body
        if isinstance(body,str):return body.encode()
        return json.dumps(body,separators=(",",":"),ensure_ascii=False).encode()
