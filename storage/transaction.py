"""
transaction.py - 事务管理器

提供事务生命周期管理：
- BEGIN / COMMIT / ROLLBACK / SAVEPOINT
- 隔离级别控制
- 全局时间戳分配
- 事务状态跟踪
"""

import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple, Any


class TxState(Enum):
    """事务状态"""
    ACTIVE = auto()
    COMMITTING = auto()
    COMMITTED = auto()
    ABORTED = auto()


class IsolationLevel(Enum):
    """SQL 隔离级别"""
    READ_UNCOMMITTED = "READ UNCOMMITTED"
    READ_COMMITTED = "READ COMMITTED"
    REPEATABLE_READ = "REPEATABLE READ"
    SERIALIZABLE = "SERIALIZABLE"


@dataclass
class Transaction:
    """事务对象"""
    xid: int                        # 事务唯一ID
    start_ts: int                   # 开始时间戳（MVCC快照读用）
    state: TxState = TxState.ACTIVE
    isolation: IsolationLevel = IsolationLevel.REPEATABLE_READ
    locks_held: Set[Tuple[str, int, str]] = field(default_factory=set)  # (table, rid, mode)
    write_set: List[Dict[str, Any]] = field(default_factory=list)       # 本事务的写操作记录
    savepoints: Dict[str, int] = field(default_factory=dict)            # name -> write_set索引

    def is_active(self) -> bool:
        return self.state == TxState.ACTIVE

    def is_committed(self) -> bool:
        return self.state == TxState.COMMITTED

    def is_aborted(self) -> bool:
        return self.state == TxState.ABORTED


class TransactionManager:
    """
    全局事务管理器

    - 分配单调递增的 xid 和 timestamp
    - 跟踪所有活跃事务
    - 协调提交/回滚流程
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._next_xid = 1
        self._global_ts = 1
        self._active_tx: Dict[int, Transaction] = {}   # xid -> Transaction
        self._committed_ts: Dict[int, int] = {}        # xid -> commit_timestamp

    # ---------- 时间戳/ID 分配 ----------

    def _alloc_xid(self) -> int:
        with self._lock:
            xid = self._next_xid
            self._next_xid += 1
            return xid

    def _alloc_ts(self) -> int:
        with self._lock:
            ts = self._global_ts
            self._global_ts += 1
            return ts

    # ---------- 事务生命周期 ----------

    def begin(self, isolation: IsolationLevel = IsolationLevel.REPEATABLE_READ) -> Transaction:
        """开始新事务"""
        xid = self._alloc_xid()
        ts = self._alloc_ts()
        tx = Transaction(xid=xid, start_ts=ts, isolation=isolation)
        with self._lock:
            self._active_tx[xid] = tx
        return tx

    def commit(self, tx: Transaction) -> bool:
        """
        提交事务
        1. 标记 COMMITTING
        2. 分配提交时间戳
        3. 标记 COMMITTED
        4. 从活跃事务列表移除
        """
        if not tx.is_active():
            return False
        tx.state = TxState.COMMITTING
        commit_ts = self._alloc_ts()
        with self._lock:
            self._committed_ts[tx.xid] = commit_ts
            if tx.xid in self._active_tx:
                del self._active_tx[tx.xid]
        tx.state = TxState.COMMITTED
        return True

    def abort(self, tx: Transaction) -> bool:
        """中止（回滚）事务"""
        if not tx.is_active():
            return False
        tx.state = TxState.ABORTED
        with self._lock:
            if tx.xid in self._active_tx:
                del self._active_tx[tx.xid]
        return True

    # ---------- 查询 ----------

    def get_tx(self, xid: int) -> Optional[Transaction]:
        with self._lock:
            return self._active_tx.get(xid)

    def is_active_xid(self, xid: int) -> bool:
        """某个xid是否仍活跃（未提交也未回滚）"""
        with self._lock:
            return xid in self._active_tx

    def get_commit_ts(self, xid: int) -> Optional[int]:
        """获取某事务的提交时间戳（未提交则None）"""
        with self._lock:
            return self._committed_ts.get(xid)

    def active_transactions(self) -> List[Transaction]:
        with self._lock:
            return list(self._active_tx.values())

    def oldest_active_ts(self) -> int:
        """返回最老的活跃事务的 start_ts（用于版本清理）"""
        with self._lock:
            if not self._active_tx:
                return self._global_ts
            return min(tx.start_ts for tx in self._active_tx.values())

    # ---------- Savepoint ----------

    def set_savepoint(self, tx: Transaction, name: str) -> None:
        """在当前write_set位置设置保存点"""
        tx.savepoints[name] = len(tx.write_set)

    def rollback_to_savepoint(self, tx: Transaction, name: str) -> bool:
        """回滚到保存点（返回是否需要继续回滚）"""
        if name not in tx.savepoints:
            return False
        idx = tx.savepoints[name]
        # 截断 write_set
        tx.write_set = tx.write_set[:idx]
        # 删除该保存点之后的所有保存点
        to_remove = [k for k, v in tx.savepoints.items() if v > idx]
        for k in to_remove:
            del tx.savepoints[k]
        return True
