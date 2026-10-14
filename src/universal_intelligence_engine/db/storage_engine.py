"""Async SQLite/PostgreSQL storage boundary for UIE repositories."""
from __future__ import annotations
import os, re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
@dataclass(frozen=True)
class StorageResult:
    rowcount: int = 0
    lastrowid: int | None = None
class StorageEngine:
    def __init__(self, url: str | None = None, *, sqlite_path: str | None = None, pool: Any = None):
        self.url = url or os.getenv('UIE_DATABASE_URL') or ('sqlite:///' + (sqlite_path or os.getenv('UIE_SQLITE_PATH', '.data/uie.sqlite3')))
        self._pool, self._connection = pool, None
        if self.url.startswith(('postgres://','postgresql://','postgresql+asyncpg://')): self.dialect='postgresql'
        elif self.url.startswith('sqlite:///') or self.url in (':memory:','sqlite:///:memory:'): self.dialect='sqlite'
        else: raise ValueError('UIE_DATABASE_URL must use PostgreSQL or SQLite')
    @classmethod
    def from_env(cls): return cls()
    def _sqlite_path(self):
        if self.url in (':memory:','sqlite:///:memory:'): return ':memory:'
        path=self.url.removeprefix('sqlite:///'); Path(path).parent.mkdir(parents=True,exist_ok=True); return path
    async def connect(self):
        if self.dialect=='sqlite':
            import aiosqlite
            self._connection=await aiosqlite.connect(self._sqlite_path()); self._connection.row_factory=aiosqlite.Row
            await self._connection.execute('PRAGMA foreign_keys=ON')
        elif self._pool is None:
            try: import asyncpg
            except ImportError as exc: raise RuntimeError('PostgreSQL storage requires asyncpg') from exc
            self._pool=await asyncpg.create_pool(dsn=re.sub(r'^postgresql\+asyncpg://','postgresql://',self.url),min_size=1,max_size=5,command_timeout=15)
        return self
    async def close(self):
        if self._connection is not None: await self._connection.close(); self._connection=None
        if self._pool is not None and hasattr(self._pool,'close'): await self._pool.close(); self._pool=None
    async def execute(self,sql,params=()):
        if self.dialect=='sqlite':
            if self._connection is None: raise RuntimeError('call connect() before queries')
            cur=await self._connection.execute(sql,tuple(params)); await self._connection.commit(); result=StorageResult(cur.rowcount,cur.lastrowid); await cur.close(); return result
        async with self._pool.acquire() as conn: status=await conn.execute(_pg_sql(sql),*params)
        match=re.search(r'(?:INSERT\s+0\s+|(?:UPDATE|DELETE)\s+)(\d+)$',status); return StorageResult(int(match.group(1)) if match else 0)
    async def fetchall(self,sql,params=()):
        if self.dialect=='sqlite':
            if self._connection is None: raise RuntimeError('call connect() before queries')
            cur=await self._connection.execute(sql,tuple(params)); rows=await cur.fetchall(); await cur.close(); return rows
        async with self._pool.acquire() as conn: return list(await conn.fetch(_pg_sql(sql),*params))
    async def fetchone(self,sql,params=()):
        rows=await self.fetchall(sql,params); return rows[0] if rows else None
    async def apply_migrations(self):
        folder=Path(__file__).with_name('migrations'); filename='001_initial_cognitive_schema.postgres.sql' if self.dialect=='postgresql' else '001_initial_cognitive_schema.sql'; script=(folder/filename).read_text(encoding='utf-8-sig')
        if self.dialect=='sqlite':
            if self._connection is None: raise RuntimeError('call connect() before migrations')
            await self._connection.executescript(script); await self._connection.commit()
        else:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    for stmt in (s.strip() for s in script.split(';')):
                        if stmt: await conn.execute(stmt)
def _pg_sql(sql):
    out=[]; quote=None; n=0; i=0
    while i<len(sql):
        c=sql[i]
        if quote:
            out.append(c)
            if c==quote:
                if i+1<len(sql) and sql[i+1]==quote: out.append(sql[i+1]); i+=1
                else: quote=None
        elif c in ("'",'"'): quote=c; out.append(c)
        elif c=='?': n+=1; out.append(f'${n}')
        else: out.append(c)
        i+=1
    return ''.join(out)
