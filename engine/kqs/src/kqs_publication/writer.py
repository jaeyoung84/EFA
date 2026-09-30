"""Single-writer lock for one publication DB on one host's local disk (advisory flock)."""

import os
from pathlib import Path

from .contract import KqsContractError


class WriterBusy(KqsContractError):
    """Another process already holds the writer lock for this publication DB."""


class WriterLock:
    """Exclusive, non-blocking lock bound to one resolved publication DB path.

    The lock file sits next to the DB and is never deleted: unlinking a lock file while
    another process has it open would let two writers lock different inodes. The kernel
    releases the lock when the holder exits, so a crashed worker does not leave it held.
    """

    def __init__(self, snapshot_db: Path):
        self.snapshot_db = Path(snapshot_db).resolve()
        self.path = self.snapshot_db.with_name(self.snapshot_db.name + ".writer.lock")
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> "WriterLock":
        import fcntl

        if self._fd is not None:
            raise KqsContractError("이미 보유한 게시 writer 잠금입니다.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise WriterBusy("다른 추천 게시 worker가 같은 게시 DB를 쓰는 중입니다.") from None
        except BaseException:
            os.close(descriptor)
            raise
        self._fd = descriptor
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def require(self, snapshot_db: Path) -> None:
        if not self.held or Path(snapshot_db).resolve() != self.snapshot_db:
            raise KqsContractError("게시는 같은 게시 DB의 단일 writer 잠금을 보유한 경우에만 가능합니다.")

    def __enter__(self) -> "WriterLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
