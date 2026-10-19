from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, Union

from totedb import BusyError, ClaimToken, Database, Queue, StaleError

ENGINES = ("totedb", "sqlite-delete", "sqlite-wal")
NOW_MS = 1_000_000
LEASE_MS = 600_000
T = TypeVar("T")


class ContentionError(Exception):
    pass


class RetryableBusyError(ContentionError):
    pass


@dataclass(frozen=True)
class Claim:
    id: int
    fence: int
    attempts: int
    payload: bytes


@dataclass
class Retry:
    busy: int = 0
    sleep_s: float = 0.0
    timeout_s: float = 60.0

    def call(self, operation: Callable[[], T]) -> T:
        deadline = time.monotonic() + self.timeout_s
        delay = 0.0005
        while True:
            try:
                return operation()
            except RetryableBusyError:
                self.busy += 1
                if time.monotonic() >= deadline:
                    raise
            before = time.perf_counter()
            time.sleep(delay)
            self.sleep_s += time.perf_counter() - before
            delay = min(delay * 2, 0.02)


class ToteAdapter:
    def __init__(self, path: Path, *, install: bool = False) -> None:
        self.clock = NOW_MS
        self.db = Database(path, busy_timeout_s=0)
        self.queue = Queue(self.db, "jobs", install=install, clock=lambda: self.clock)
        if install:
            self.db.execute(
                "CREATE TABLE business (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)"
            )

    def close(self) -> None:
        self.db.close()

    def put(self, payload: bytes, retry: Retry) -> int:
        def operation() -> int:
            try:
                return self.queue.put(payload).id
            except BusyError as error:
                raise RetryableBusyError(str(error)) from error

        return retry.call(operation)

    def outbox(
        self, index: int, payload: bytes, retry: Retry, *, rollback: bool = False
    ) -> int:
        def begin() -> object:
            try:
                return self.db.begin()
            except BusyError as error:
                raise RetryableBusyError(str(error)) from error

        txn = retry.call(begin)
        with txn:
            txn.execute(
                "INSERT INTO business (id, payload) VALUES (?, ?)", (index, payload)
            )
            item_id = self.queue.put(payload, txn=txn).id
            if rollback:
                txn.rollback()
        return item_id

    def claim(self, retry: Retry) -> Claim | None:
        def operation() -> Claim | None:
            try:
                item = self.queue.get(LEASE_MS)
            except BusyError as error:
                raise RetryableBusyError(str(error)) from error
            if item is None:
                return None
            return Claim(item.id, item.fence, item.attempts, item.payload)

        return retry.call(operation)

    def ack(self, item: Claim, retry: Retry) -> None:
        def operation() -> None:
            try:
                self.queue.ack(ClaimToken(item.id, item.fence))
            except BusyError as error:
                raise RetryableBusyError(str(error)) from error

        retry.call(operation)

    def rows(self) -> list[tuple[int, int, int, int, bytes]]:
        return [
            (r.id, int(r.state), r.fence, r.attempts, r.payload)
            for r in self.queue.iter_items(with_payload=True)
        ]

    def business(self) -> list[tuple[int, bytes]]:
        return list(
            self.db.execute("SELECT id, payload FROM business ORDER BY id").rows
        )

    def settings(self) -> dict[str, object]:
        return {
            "api": "public Python Database/Queue",
            "busy_timeout_s": self.db.busy_timeout_s,
            "durability": "COW durable commit",
            "clock_ms": self.clock,
            "lease_ms": LEASE_MS,
        }


class SQLiteAdapter:
    def __init__(self, path: Path, engine: str, *, install: bool = False) -> None:
        self.clock = NOW_MS
        self.engine = engine
        self.db = sqlite3.connect(
            path, timeout=0, isolation_level=None, cached_statements=128
        )
        self.db.execute("PRAGMA page_size=8192")
        if install:
            self.db.execute(
                "PRAGMA journal_mode=" + ("WAL" if engine == "sqlite-wal" else "DELETE")
            )
        for pragma in (
            "synchronous=FULL",
            "fullfsync=OFF",
            "cache_size=256",
            "mmap_size=0",
            "busy_timeout=0",
            "wal_autocheckpoint=1000",
        ):
            self.db.execute("PRAGMA " + pragma)
        if install:
            self.db.executescript(
                "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, state INTEGER NOT NULL, "
                "fence INTEGER NOT NULL, attempts INTEGER NOT NULL, ready_at INTEGER NOT NULL, "
                "lease_until INTEGER NOT NULL, payload BLOB NOT NULL);"
                "CREATE INDEX jobs_ready ON jobs(state,id,ready_at);"
                "CREATE INDEX jobs_expired ON jobs(state,id,lease_until);"
                "CREATE TABLE business(id INTEGER PRIMARY KEY,payload BLOB NOT NULL);"
            )

    def close(self) -> None:
        self.db.close()

    def put(self, payload: bytes, retry: Retry) -> int:
        return self._transaction(lambda: self._insert(payload), retry)

    def outbox(
        self, index: int, payload: bytes, retry: Retry, *, rollback: bool = False
    ) -> int:
        def insert() -> int:
            self.db.execute(
                "INSERT INTO business(id,payload) VALUES (?,?)", (index, payload)
            )
            return self._insert(payload)

        return self._transaction(insert, retry, rollback=rollback)

    def claim(self, retry: Retry) -> Claim | None:
        def select() -> Claim | None:
            candidates = []
            for state, column, index in (
                (0, "ready_at", "jobs_ready"),
                (1, "lease_until", "jobs_expired"),
            ):
                row = self.db.execute(
                    f"SELECT id FROM jobs INDEXED BY {index} WHERE state=? AND {column}<=? ORDER BY id LIMIT 1",
                    (state, self.clock),
                ).fetchone()
                if row is not None:
                    candidates.append(row[0])
            if not candidates:
                return None
            item_id = min(candidates)
            self.db.execute(
                "UPDATE jobs SET state=1,fence=fence+1,attempts=attempts+1,lease_until=? WHERE id=?",
                (self.clock + LEASE_MS, item_id),
            )
            fence, attempts, payload = self.db.execute(
                "SELECT fence,attempts,payload FROM jobs WHERE id=?", (item_id,)
            ).fetchone()
            return Claim(item_id, fence, attempts, payload)

        return self._transaction(select, retry)

    def ack(self, item: Claim, retry: Retry) -> None:
        def update() -> None:
            row = self.db.execute(
                "SELECT state,fence FROM jobs WHERE id=?", (item.id,)
            ).fetchone()
            if row is None or row[1] != item.fence or row[0] not in (1, 2):
                raise StaleError("stale claim")
            self.db.execute(
                "UPDATE jobs SET state=2,lease_until=0 WHERE id=?", (item.id,)
            )

        self._transaction(update, retry)

    def rows(self) -> list[tuple[int, int, int, int, bytes]]:
        return self.db.execute(
            "SELECT id,state,fence,attempts,payload FROM jobs ORDER BY id"
        ).fetchall()

    def business(self) -> list[tuple[int, bytes]]:
        return self.db.execute("SELECT id,payload FROM business ORDER BY id").fetchall()

    def settings(self) -> dict[str, object]:
        names = (
            "journal_mode",
            "synchronous",
            "fullfsync",
            "page_size",
            "cache_size",
            "mmap_size",
            "busy_timeout",
            "wal_autocheckpoint",
        )
        settings = {
            **{name: self.db.execute("PRAGMA " + name).fetchone()[0] for name in names},
            "statement_cache": 128,
            "sqlite_version": sqlite3.sqlite_version,
            "clock_ms": self.clock,
            "lease_ms": LEASE_MS,
        }
        expected = {
            "journal_mode": "wal" if self.engine == "sqlite-wal" else "delete",
            "synchronous": 2,
            "fullfsync": 0,
            "page_size": 8192,
            "cache_size": 256,
            "mmap_size": 0,
            "busy_timeout": 0,
            "wal_autocheckpoint": 1000,
        }
        if any(settings[name] != value for name, value in expected.items()):
            raise ContentionError(
                "effective SQLite settings do not match requested settings"
            )
        plans = {}
        for state, column, index in (
            (0, "ready_at", "jobs_ready"),
            (1, "lease_until", "jobs_expired"),
        ):
            plan = self.db.execute(
                f"EXPLAIN QUERY PLAN SELECT id FROM jobs INDEXED BY {index} "
                f"WHERE state=? AND {column}<=? ORDER BY id LIMIT 1",
                (state, self.clock),
            ).fetchall()
            if not any(index in row[3] and "SEARCH" in row[3] for row in plan):
                raise ContentionError(
                    "claim query does not use the expected state/id index"
                )
            plans[index] = plan
        settings["claim_query_plans"] = plans
        return settings

    def _insert(self, payload: bytes) -> int:
        return self.db.execute(
            "INSERT INTO jobs(state,fence,attempts,ready_at,lease_until,payload) VALUES (0,0,0,?,0,?)",
            (self.clock, payload),
        ).lastrowid

    def _transaction(
        self, operation: Callable[[], T], retry: Retry, *, rollback: bool = False
    ) -> T:
        def begin() -> None:
            self._busy_sql("BEGIN IMMEDIATE")

        retry.call(begin)
        try:
            result = operation()
            if rollback:
                self.db.execute("ROLLBACK")
            else:
                retry.call(lambda: self._busy_sql("COMMIT"))
            return result
        finally:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")

    def _busy_sql(self, sql: str) -> None:
        try:
            self.db.execute(sql)
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", None)
            if code == 5 or (code is None and str(error) == "database is locked"):
                raise RetryableBusyError(str(error)) from error
            raise


Adapter = Union[ToteAdapter, SQLiteAdapter]


def open_adapter(path: Path, engine: str, *, install: bool = False) -> Adapter:
    if engine == "totedb":
        return ToteAdapter(path, install=install)
    if engine not in ENGINES:
        raise ContentionError("unknown engine: " + engine)
    return SQLiteAdapter(path, engine, install=install)


def payload(index: int, size: int) -> bytes:
    if size < 8:
        raise ContentionError("payload must contain at least eight bytes")
    prefix = index.to_bytes(8, "big")
    return prefix + hashlib.shake_256(b"queue-contention-226" + prefix).digest(size - 8)
