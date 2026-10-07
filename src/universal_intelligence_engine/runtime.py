"""Unified sense/act runtime with structured audit events."""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Any,Mapping
from .eyes import Observation,WebObserver
from .hands import ActionResult,HttpHand

@dataclass(frozen=True)
class RuntimeEvent:
    kind:str
    capability:str
    target:str
    status:str
    details:Mapping[str,Any]=field(default_factory=dict)

class SenseActRuntime:
    """The stable boundary between UIE cognition and external-world I/O."""
    def __init__(self,*,observer:WebObserver|None=None,http_hand:HttpHand|None=None):
        self.observer=observer or WebObserver(); self.http_hand=http_hand; self.events:list[RuntimeEvent]=[]

    def observe_url(self,url:str)->Observation:
        result=self.observer.observe(url)
        self.events.append(RuntimeEvent("observation","web.observe",url,result.status,
            {"status_code":result.status_code,"content_sha256":result.content_sha256,"truncated":result.truncated}))
        return result

    def execute_http(self,*,method:str,url:str,body:object|None=None,headers:Mapping[str,str]|None=None,approved:bool=False)->ActionResult:
        if self.http_hand is None: raise PermissionError("HTTP hand is not configured")
        result=self.http_hand.execute(method=method,url=url,body=body,headers=headers,approved=approved)
        self.events.append(RuntimeEvent("action","http.execute",url,result.status,
            {"method":result.method,"status_code":result.status_code,"attempts":result.attempts}))
        return result

    def audit(self)->tuple[RuntimeEvent,...]: return tuple(self.events)
