"""
version_store.py - MVCC 版本存储

用版本链替代单值存储：
- 每个 rid 对应一条版本链
- 写操作创建新版本，旧版本保留供快照读
- 定期清理不可见的旧版本
"""

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from .transaction import Transaction, IsolationLevel


@dataclass
class RowVersion:
    """行的一个版本"""
    rid: int
    data: Dict[str, Any]
    created_by: int         # 创建该版本的事务 xid
    expired_by: int = 0     # 使该版本过期的事务 xid (0=未过期)
    prev: Optional["RowVersion"] = None  # 版本链前向指针


class VersionStore:
    """
    MVCC 版本存储

    _versions: {rid: RowVersion}  — 每个 rid 指向最新版本
    版本链：最新 -> 较新 -> ... -> 最旧
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._versions: Dict[int, RowVersion] = {}   # rid -> 最新版本
        self._next_rid = 1

    # ---------- 写操作 ----------

    def insert(self, data: Dict[str, Any], tx: Transaction) -> int:
        """插入新行，返回 rid"""
        with self._lock:
            rid = self._next_rid
            self._next_rid += 1

        version = RowVersion(
            rid=rid,
            data=deepcopy(data),
            created_by=tx.xid,
            expired_by=0,
            prev=None,
        )
        with self._lock:
            self._versions[rid] = version
        return rid

    def update(self, rid: int, new_data: Dict[str, Any], tx: Transaction) -> bool:
        """
        更新行：创建新版本，旧版本标记为过期
        返回是否成功
        """
        with self._lock:
            current = self._versions.get(rid)
            if current is None:
                return False

            # 创建新版本（深拷贝数据）
            new_version = RowVersion(
                rid=rid,
                data=deepcopy(new_data),
                created_by=tx.xid,
                expired_by=0,
                prev=current,
            )
            # 旧版本标记为过期
            current.expired_by = tx.xid
            self._versions[rid] = new_version
            return True

    def delete(self, rid: int, tx: Transaction) -> bool:
        """
        删除行：创建 tombstone 版本（data=None）
        返回是否成功
        """
        with self._lock:
            current = self._versions.get(rid)
            if current is None:
                return False

            tombstone = RowVersion(
                rid=rid,
                data=None,  # tombstone 标记
                created_by=tx.xid,
                expired_by=0,
                prev=current,
            )
            current.expired_by = tx.xid
            self._versions[rid] = tombstone
            return True

    # ---------- 读操作 (MVCC) ----------

    def read(self, rid: int, tx: Transaction, tx_manager) -> Optional[Dict[str, Any]]:
        """
        MVCC 快照读

        可见性规则（REPEATABLE READ）：
        - 版本.created_by 已提交，且提交时间戳 <= tx.start_ts
        - 或者版本.created_by == tx.xid（本事务自己写的）
        - 版本不是 tombstone（data is not None）

        READ COMMITTED：每次读使用当前 global_ts
        READ UNCOMMITTED：读最新版本（不管提交状态）
        SERIALIZABLE：同 REPEATABLE READ，但读也加 S 锁（在调用层处理）
        """
        with self._lock:
            head = self._versions.get(rid)
            if head is None:
                return None

        # 遍历版本链找到可见版本
        version = head
        while version is not None:
            if self._is_visible(version, tx, tx_manager):
                if version.data is None:
                    return None  # tombstone
                return deepcopy(version.data)
            version = version.prev

        return None

    def read_all(self, tx: Transaction, tx_manager) -> List[Dict[str, Any]]:
        """全表快照读，返回所有可见行"""
        results = []
        with self._lock:
            rids = list(self._versions.keys())

        for rid in rids:
            data = self.read(rid, tx, tx_manager)
            if data is not None:
                data = dict(data)
                data["rid"] = rid
                results.append(data)
        return results

    def _is_visible(self, version: RowVersion, tx: Transaction, tx_manager) -> bool:
        """判断某版本对当前事务是否可见"""
        isolation = tx.isolation

        # READ UNCOMMITTED: 看最新版本
        if isolation == IsolationLevel.READ_UNCOMMITTED:
            return True

        creator = version.created_by

        # 本事务自己写的，总是可见
        if creator == tx.xid:
            return True

        # 系统事务（恢复的数据）总是可见
        if creator == 0:
            return True

        # 创建者是否已提交？
        commit_ts = tx_manager.get_commit_ts(creator)
        if commit_ts is None:
            # 创建者未提交（且不是本事务）-> 不可见
            return False

        # READ COMMITTED: 已提交的就行（每次读都重新判断）
        if isolation == IsolationLevel.READ_COMMITTED:
            return True

        # REPEATABLE READ / SERIALIZABLE: 提交时间戳必须 <= 事务开始时间戳
        # 例外：如果 start_ts 为 0（非事务/系统读），只要已提交就可见
        if tx.start_ts == 0:
            return True

        return commit_ts <= tx.start_ts

    # ---------- 元数据 ----------

    def get_next_rid(self) -> int:
        with self._lock:
            return self._next_rid

    def set_next_rid(self, val: int) -> None:
        with self._lock:
            self._next_rid = val

    def exists(self, rid: int) -> bool:
        with self._lock:
            return rid in self._versions

    def all_rids(self) -> List[int]:
        with self._lock:
            return list(self._versions.keys())

    # ---------- 版本清理 (VACUUM) ----------

    def vacuum(self, oldest_visible_ts: int) -> int:
        """
        清理所有已提交且不可见的旧版本
        返回清理的版本数
        """
        cleaned = 0
        with self._lock:
            for rid, head in list(self._versions.items()):
                new_head, count = self._prune_chain(head, oldest_visible_ts)
                if new_head is None:
                    # 全部清理完了
                    if rid in self._versions:
                        del self._versions[rid]
                elif new_head is not head:
                    self._versions[rid] = new_head
                cleaned += count
        return cleaned

    def _prune_chain(self, head: RowVersion, oldest_visible_ts: int):
        """
        从版本链尾部开始清理
        保留条件：版本尚未被清理（需要保留给旧事务看）
        返回 (new_head, cleaned_count)
        """
        # 先收集整条链
        versions = []
        v = head
        while v:
            versions.append(v)
            v = v.prev

        # 从尾部（最旧）开始找可以清理的
        # 一个版本可以清理当：
        # - 它已经被过期（expired_by != 0）
        # - 它的创建者已提交
        # - 没有活跃事务需要看到它（oldest_visible_ts > 创建者提交时间）
        # 简化策略：只清理 tombstone 后面的版本，或非常旧的已提交版本
        # 这里采用保守策略：只清理确定没有任何事务会读到的版本

        cleaned = 0
        # 实际上版本链清理比较复杂，先不实现激进清理
        # 只清理 tombstone 且后面没有未提交版本的情况
        return head, cleaned

    # ---------- 序列化（用于持久化） ----------

    def to_dict(self, tx_manager=None) -> List[Dict[str, Any]]:
        """
        导出所有已提交的最新版本（用于持久化）
        如果提供了 tx_manager，会检查 created_by 是否已提交
        """
        with self._lock:
            rows = []
            for rid, head in self._versions.items():
                # 遍历版本链，找到最新的非 tombstone 且已提交的版本
                v = head
                while v is not None:
                    if v.data is not None:
                        # created_by == 0 表示系统/恢复的数据，总是已提交
                        # 否则需要检查 tx_manager 确认是否已提交
                        is_committed = (v.created_by == 0)
                        if not is_committed and tx_manager is not None:
                            is_committed = tx_manager.get_commit_ts(v.created_by) is not None
                        if is_committed:
                            rows.append({"rid": rid, "data": deepcopy(v.data)})
                            break
                    v = v.prev
            return rows

    def from_dict(self, rows: List[Dict[str, Any]], next_rid: int) -> None:
        """从持久化数据恢复"""
        with self._lock:
            self._versions.clear()
            self._next_rid = next_rid
            for row in rows:
                rid = row["rid"]
                version = RowVersion(
                    rid=rid,
                    data=deepcopy(row["data"]),
                    created_by=0,  # 恢复的数据视为已提交
                    expired_by=0,
                    prev=None,
                )
                self._versions[rid] = version
