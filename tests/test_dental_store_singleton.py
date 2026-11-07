import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from universal_intelligence_engine import dental_store


def test_get_dental_store_initializes_only_once_for_concurrent_requests(monkeypatch):
    barrier = Barrier(8)
    created = []
    created_lock = Lock()

    class FakeStore:
        def __init__(self):
            with created_lock:
                created.append(self)
            time.sleep(0.02)

    monkeypatch.setattr(dental_store, "DentalStore", FakeStore)
    monkeypatch.setattr(dental_store, "_store", None)

    def get_store():
        barrier.wait(timeout=3)
        return dental_store.get_dental_store()

    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: get_store(), range(8)))

    assert len(created) == 1
    assert all(store is created[0] for store in stores)
