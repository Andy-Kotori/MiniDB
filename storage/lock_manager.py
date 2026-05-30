"""
lock_manager.py - 行级锁管理器

支持：
- 共享锁(S) / 排他锁(X)
- 意向锁(IS/IX) 用于表级意向
- 锁等待队列 + 超时
- 等待图死锁检测
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class LockMode(Enum):
    """锁模式"""
    S = "SHARED"           # 共享锁（读）
    X = "EXCLUSIVE"        # 排他锁（写）
    IS = "INTENT_SHARED"   # 意向共享锁
    IX = "INTENT_EXCLUSIVE"# 意向排他锁


# 锁兼容矩阵：行(row, rid>=0) 和 表(table, rid=-1) 级别
#  True = 兼容（可以同时持有）
_LOCK_COMPATIBILITY: Dict[Tuple[LockMode, LockMode], bool] = {
    (LockMode.S, LockMode.S): True,
    (LockMode.S, LockMode.X): False,
    (LockMode.S, LockMode.IS): True,
    (LockMode.S, LockMode.IX): True,

    (LockMode.X, LockMode.S): False,
    (LockMode.X, LockMode.X): False,
    (LockMode.X, LockMode.IS): False,
    (LockMode.X, LockMode.IX): False,

    (LockMode.IS, LockMode.S): True,
    (LockMode.IS, LockMode.X): False,
    (LockMode.IS, LockMode.IS): True,
    (LockMode.IS, LockMode.IX): True,

    (LockMode.IX, LockMode.S): True,
    (LockMode.IX, LockMode.X): False,
    (LockMode.IX, LockMode.IS): True,
    (LockMode.IX, LockMode.IX): True,
}


def _compatible(mode1: LockMode, mode2: LockMode) -> bool:
    return _LOCK_COMPATIBILITY.get((mode1, mode2), False)


@dataclass
class LockRequest:
    """锁请求"""
    xid: int
    mode: LockMode
    granted: bool = False
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class LockEntry:
    """某个资源上的锁状态"""
    holders: Dict[int, LockMode] = field(default_factory=dict)      # xid -> mode
    waiters: List[LockRequest] = field(default_factory=list)        # 等待队列


class LockManager:
    """
    行级锁管理器

    资源标识：(table_name, rid)
    - rid >= 0: 行级锁
    - rid == -1: 表级意向锁
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._locks: Dict[Tuple[str, int], LockEntry] = {}   # (table, rid) -> LockEntry
        self._tx_locks: Dict[int, Set[Tuple[str, int]]] = {} # xid -> {(table, rid), ...}

    # ---------- 加锁 / 解锁 ----------

    def acquire(
        self,
        xid: int,
        table: str,
        rid: int,
        mode: LockMode,
        timeout: float = 5.0,
    ) -> bool:
        """
        获取锁，支持超时
        返回 True=成功, False=超时
        """
        key = (table, rid)

        with self._lock:
            entry = self._locks.setdefault(key, LockEntry())

            # 检查是否已持有兼容锁（升级或重复获取）
            if xid in entry.holders:
                held_mode = entry.holders[xid]
                if held_mode == mode:
                    return True  # 已持有同类型锁
                # 锁升级：S -> X
                if held_mode == LockMode.S and mode == LockMode.X:
                    # 检查是否只有自己在持有
                    if len(entry.holders) == 1:
                        entry.holders[xid] = LockMode.X
                        self._record_tx_lock(xid, key)
                        return True
                # 其他升级不支持
                return False

            # 检查是否与现有持有者兼容
            can_grant = True
            for holder_xid, holder_mode in entry.holders.items():
                if holder_xid != xid and not _compatible(mode, holder_mode):
                    can_grant = False
                    break

            if can_grant:
                entry.holders[xid] = mode
                self._record_tx_lock(xid, key)
                return True

            # 需要等待
            req = LockRequest(xid=xid, mode=mode)
            entry.waiters.append(req)

        # 在锁外等待（避免死锁）
        granted = req.event.wait(timeout=timeout)

        if not granted:
            # 超时，从等待队列移除
            with self._lock:
                if req in entry.waiters:
                    entry.waiters.remove(req)
            return False

        self._record_tx_lock(xid, key)
        return True

    def release(self, xid: int, table: str, rid: int) -> bool:
        """释放单个锁"""
        key = (table, rid)
        with self._lock:
            entry = self._locks.get(key)
            if not entry:
                return False
            if xid not in entry.holders:
                return False

            del entry.holders[xid]
            self._unrecord_tx_lock(xid, key)

            # 尝试唤醒等待者
            self._wake_waiters(entry)

            # 清理空entry
            if not entry.holders and not entry.waiters:
                del self._locks[key]
            return True

    def release_all(self, xid: int) -> None:
        """释放某事务持有的所有锁"""
        with self._lock:
            keys = list(self._tx_locks.get(xid, set()))
            for key in keys:
                entry = self._locks.get(key)
                if entry and xid in entry.holders:
                    del entry.holders[xid]
                    self._wake_waiters(entry)
                    if not entry.holders and not entry.waiters:
                        del self._locks[key]
            if xid in self._tx_locks:
                del self._tx_locks[xid]

    # ---------- 内部辅助 ----------

    def _record_tx_lock(self, xid: int, key: Tuple[str, int]) -> None:
        self._tx_locks.setdefault(xid, set()).add(key)

    def _unrecord_tx_lock(self, xid: int, key: Tuple[str, int]) -> None:
        s = self._tx_locks.get(xid)
        if s:
            s.discard(key)
            if not s:
                del self._tx_locks[xid]

    def _wake_waiters(self, entry: LockEntry) -> None:
        """唤醒等待队列中所有可以被满足的等待者"""
        to_wake = []
        for req in list(entry.waiters):
            can_grant = True
            for holder_mode in entry.holders.values():
                if not _compatible(req.mode, holder_mode):
                    can_grant = False
                    break
            if can_grant:
                entry.holders[req.xid] = req.mode
                req.granted = True
                to_wake.append(req)
                entry.waiters.remove(req)

        # 在锁外触发event（避免持有锁时回调）
        for req in to_wake:
            req.event.set()

    # ---------- 死锁检测 ----------

    def detect_deadlock(self) -> Optional[List[int]]:
        """
        等待图检测死锁
        返回死锁环中的 xid 列表，无死锁返回 None
        """
        with self._lock:
            # 构建等待图: xid -> set(等待的xid)
            wait_graph: Dict[int, Set[int]] = {}
            for key, entry in self._locks.items():
                holders = set(entry.holders.keys())
                for req in entry.waiters:
                    if req.xid not in wait_graph:
                        wait_graph[req.xid] = set()
                    wait_graph[req.xid].update(holders)

            # DFS找环
            visited = set()
            rec_stack = set()
            path: List[int] = []

            def dfs(node: int) -> Optional[List[int]]:
                visited.add(node)
                rec_stack.add(node)
                path.append(node)
                for neighbor in wait_graph.get(node, set()):
                    if neighbor not in visited:
                        result = dfs(neighbor)
                        if result:
                            return result
                    elif neighbor in rec_stack:
                        # 找到环
                        cycle_start = path.index(neighbor)
                        return path[cycle_start:]
                path.pop()
                rec_stack.remove(node)
                return None

            for node in list(wait_graph.keys()):
                if node not in visited:
                    result = dfs(node)
                    if result:
                        return result
            return None

    def get_lock_info(self) -> Dict[str, Any]:
        """调试：返回当前锁状态"""
        with self._lock:
            info = {}
            for (table, rid), entry in self._locks.items():
                key = f"{table}:{rid}"
                info[key] = {
                    "holders": {str(k): v.value for k, v in entry.holders.items()},
                    "waiters": [{"xid": w.xid, "mode": w.mode.value} for w in entry.waiters],
                }
            return info
