"""Lightweight semantic similarity: optional embeddings, otherwise hashed token/ngram vectors."""
from __future__ import annotations
import hashlib, math, re, sqlite3, struct
from pathlib import Path
from typing import Callable, Iterable
try:
    import numpy as np
except ImportError:
    np = None
VECTOR_DIMENSIONS = 384

def _features(text: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    tokens = normalized.split()
    features = ["w:" + token for token in tokens]
    for token in tokens:
        padded = "^" + token + "$"
        features.extend("c:" + padded[i:i+n] for n in (3,4) for i in range(max(0,len(padded)-n+1)))
    synonyms = {"abscess":("purulent infection","localized pus collection","acute apical abscess"),
      "periapical":("apical","root tip","periradicular"),"swelling":("edema","inflammation","enlargement"),
      "tooth":("dental","dentition"),"acute":("sudden","rapid onset")}
    for token in tokens: features.extend("s:"+phrase for phrase in synonyms.get(token,()))
    return features

def hashed_vector(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    if dimensions < 1: raise ValueError("dimensions must be positive")
    vector=[0.0]*dimensions
    for feature in _features(text):
        digest=hashlib.blake2b(feature.encode("utf-8"),digest_size=8).digest()
        value=int.from_bytes(digest,"little"); vector[value%dimensions] += -1.0 if (value>>8)&1 else 1.0
    return vector

def cosine_similarity(u: Iterable[float], v: Iterable[float]) -> float:
    left,right=list(u),list(v)
    if len(left)!=len(right) or not left: return 0.0
    if np is not None:
        a,b=np.asarray(left,dtype=np.float32),np.asarray(right,dtype=np.float32)
        denom=float(np.linalg.norm(a)*np.linalg.norm(b))
        return float(np.dot(a,b)/denom) if denom else 0.0
    dot=sum(a*b for a,b in zip(left,right))
    denom=math.sqrt(sum(a*a for a in left)*sum(b*b for b in right))
    return dot/denom if denom else 0.0

def encode_float32(vector: Iterable[float]) -> bytes:
    values=[float(value) for value in vector]
    return struct.pack("<"+"f"*len(values),*values)

def decode_float32(blob: bytes, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    if len(blob)!=dimensions*4: raise ValueError(f"expected {dimensions*4} bytes for {dimensions} float32 values")
    return list(struct.unpack("<"+"f"*dimensions,blob))

class SemanticMatcher:
    def __init__(self, embedder: Callable[[str],Iterable[float]]|None=None, threshold: float=0.18, dimensions: int=VECTOR_DIMENSIONS):
        self.embedder=embedder; self.threshold=max(0.0,min(1.0,float(threshold))); self.dimensions=dimensions
    def vectorize(self,text: str)->list[float]:
        if self.embedder is not None:
            vector=[float(x) for x in self.embedder(text)]
            if len(vector)!=self.dimensions: raise ValueError(f"embedding must contain {self.dimensions} dimensions")
            return vector
        return hashed_vector(text,self.dimensions)
    def similarity(self,left: str,right: str)->float: return cosine_similarity(self.vectorize(left),self.vectorize(right))
    def match(self,query: str,candidates: Iterable[dict],text_fields: tuple[str,...]=("subject","predicate","object_claim"),threshold: float|None=None,limit: int=20)->list[dict]:
        floor=self.threshold if threshold is None else max(0.0,min(1.0,float(threshold)))
        query_vector=self.vectorize(query); matches=[]
        for candidate in candidates:
            text=" ".join(str(candidate.get(field,"")) for field in text_fields)
            if not text.strip(): continue
            score=cosine_similarity(query_vector,self.vectorize(text))
            if score>=floor:
                item=dict(candidate); item["semantic_score"]=round(score,6); matches.append(item)
        matches.sort(key=lambda item:item["semantic_score"],reverse=True)
        return matches[:max(1,int(limit))]
    def persist_vector(self,db_path: str|Path,record_type: str,record_id: str,text: str)->bytes:
        blob=encode_float32(self.vectorize(text))
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS semantic_vectors (
                record_type TEXT NOT NULL, record_id TEXT NOT NULL, dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(record_type,record_id))""")
            conn.execute("""INSERT INTO semantic_vectors(record_type,record_id,dimensions,vector,updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(record_type,record_id) DO UPDATE SET
                dimensions=excluded.dimensions,vector=excluded.vector,updated_at=CURRENT_TIMESTAMP""",
                (record_type,record_id,self.dimensions,blob))
        return blob
