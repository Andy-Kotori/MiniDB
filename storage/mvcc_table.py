"""
mvcc_table.py - MVCC 兼容的数据表

兼容现有 Table 的接口，但内部使用 VersionStore + LockManager
支持事务上下文操作
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional

from .database import SchemaMode
from .index import IndexManager, IndexType
from .transaction import Transaction, IsolationLevel
from .lock_manager import LockManager, LockMode
from .version_store import VersionStore
from .wal import WALManager, WALRecordType


class MvccTable:
    """
    MVCC 数据表

    与 Table 保持接口兼容，但所有写操作需要 Transaction 上下文
    """

    def __init__(self, name: str, columns: List[str], mode: SchemaMode = SchemaMode.STRICT):
        self._name = name
        self._columns = columns.copy()
        self._mode = mode
        self._version_store = VersionStore()
        self._index_manager = IndexManager()
        self._lock_manager: Optional[LockManager] = None
        self._wal: Optional[WALManager] = None
        self._tx_manager = None

    # ---------- 注入依赖 ----------

    def set_lock_manager(self, lm: LockManager) -> None:
        self._lock_manager = lm

    def set_wal(self, wal: WALManager) -> None:
        self._wal = wal

    def set_tx_manager(self, txm) -> None:
        self._tx_manager = txm

    # ---------- 属性 ----------

    @property
    def name(self) -> str:
        return self._name

    @property
    def columns(self) -> List[str]:
        return self._columns.copy()

    @property
    def row_count(self) -> int:
        # 统计非 tombstone 版本数（近似）
        return len(self._version_store.all_rids())

    @property
    def next_rid(self) -> int:
        return self._version_store.get_next_rid()

    @property
    def mode(self) -> SchemaMode:
        return self._mode

    def set_mode(self, mode: SchemaMode) -> None:
        self._mode = mode

    # ---------- 行操作（事务版本） ----------

    def insert(self, data: Dict[str, Any], tx: Transaction) -> int:
        """事务内插入"""
        if not tx.is_active():
            raise RuntimeError("Transaction is not active")

        # schema 检查
        input_cols = set(data.keys())
        existing_cols = set(self._columns)
        unknown_cols = input_cols - existing_cols

        if unknown_cols:
            if self._mode == SchemaMode.STRICT:
                raise ValueError(f"未知列: {unknown_cols}，表 '{self._name}' 的列为: {self._columns}")
            else:
                for col in unknown_cols:
                    self._add_column_to_all_rows(col, None)

        full_data = {col: data.get(col) for col in self._columns}

        # 写入版本存储
        rid = self._version_store.insert(full_data, tx)

        # 写 WAL
        if self._wal:
            self._wal.append(
                xid=tx.xid,
                record_type=WALRecordType.INSERT,
                table=self._name,
                rid=rid,
                after_image=deepcopy(full_data),
            )

        # 更新索引
        self._index_manager.on_insert(full_data, rid)

        # 记录到 write_set（用于 savepoint/rollback）
        tx.write_set.append({
            "type": "insert",
            "table": self._name,
            "rid": rid,
            "data": deepcopy(full_data),
        })

        return rid

    def get_by_rid(self, rid: int, tx: Transaction, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """MVCC 快照读（按 rid）"""
        data = self._version_store.read(rid, tx, self._tx_manager)
        if data is None:
            return None
        result = {"rid": rid}
        if columns is None:
            result.update(data)
        else:
            for col in columns:
                result[col] = data.get(col)
        return result

    def get_by_index(self, index: int, tx: Transaction, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """按索引（第几行）获取"""
        rids = self._version_store.all_rids()
        if index < 0 or index >= len(rids):
            return None
        return self.get_by_rid(rids[index], tx, columns)

    def get_all(self, tx: Transaction, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """MVCC 全表扫描"""
        results = []
        for rid in self._version_store.all_rids():
            row = self.get_by_rid(rid, tx, columns)
            if row is not None:
                results.append(row)
        return results

    def update_by_rid(self, rid: int, new_data: Dict[str, Any], tx: Transaction) -> bool:
        """事务内更新"""
        if not tx.is_active():
            raise RuntimeError("Transaction is not active")

        # 获取行级 X 锁
        if self._lock_manager:
            if not self._lock_manager.acquire(tx.xid, self._name, rid, LockMode.X):
                raise RuntimeError(f"无法获取行锁: {self._name}:{rid}")
            tx.locks_held.add((self._name, rid, "X"))

        # 读取当前版本（用于 before_image）
        current = self._version_store.read(rid, tx, self._tx_manager)
        if current is None:
            return False

        # schema 检查
        input_cols = set(new_data.keys())
        existing_cols = set(self._columns)
        unknown_cols = input_cols - existing_cols

        if unknown_cols and self._mode == SchemaMode.LOOSE:
            for col in unknown_cols:
                self._add_column_to_all_rows(col, None)

        # 构建新数据
        valid_data = {k: v for k, v in new_data.items() if k in self._columns}
        updated_data = dict(current)
        updated_data.update(valid_data)
        updated_data.pop("rid", None)

        # 写 WAL
        if self._wal:
            self._wal.append(
                xid=tx.xid,
                record_type=WALRecordType.UPDATE,
                table=self._name,
                rid=rid,
                before_image=deepcopy(current),
                after_image=deepcopy(updated_data),
            )

        # 更新索引
        old_data = dict(current)
        old_data.pop("rid", None)
        self._index_manager.on_update(old_data, valid_data, rid)

        # 写入新版本
        self._version_store.update(rid, updated_data, tx)

        tx.write_set.append({
            "type": "update",
            "table": self._name,
            "rid": rid,
            "before": deepcopy(current),
        })
        return True

    def delete_by_rid(self, rid: int, tx: Transaction) -> bool:
        """事务内删除"""
        if not tx.is_active():
            raise RuntimeError("Transaction is not active")

        # 获取行级 X 锁
        if self._lock_manager:
            if not self._lock_manager.acquire(tx.xid, self._name, rid, LockMode.X):
                raise RuntimeError(f"无法获取行锁: {self._name}:{rid}")
            tx.locks_held.add((self._name, rid, "X"))

        current = self._version_store.read(rid, tx, self._tx_manager)
        if current is None:
            return False

        # 写 WAL
        if self._wal:
            self._wal.append(
                xid=tx.xid,
                record_type=WALRecordType.DELETE,
                table=self._name,
                rid=rid,
                before_image=deepcopy(current),
            )

        # 更新索引
        old_data = dict(current)
        old_data.pop("rid", None)
        self._index_manager.on_delete(old_data, rid)

        # 写入 tombstone
        self._version_store.delete(rid, tx)

        tx.write_set.append({
            "type": "delete",
            "table": self._name,
            "rid": rid,
            "before": deepcopy(current),
        })
        return True

    # ---------- Undo 操作（用于 savepoint/rollback） ----------

    def undo_operations(self, tx: Transaction, from_idx: int) -> None:
        """
        从事务 write_set 的 from_idx 位置开始，逆序撤销操作
        """
        ops = tx.write_set[from_idx:]
        for op in reversed(ops):
            op_type = op["type"]
            rid = op["rid"]

            if op_type == "insert":
                # Undo insert: 删除插入的行
                self._version_store.delete(rid, tx)
                # 删除索引
                data = op.get("data", {})
                self._index_manager.on_delete(data, rid)

            elif op_type == "update":
                # Undo update: 恢复 before_image
                before = op.get("before")
                if before:
                    before_copy = dict(before)
                    before_copy.pop("rid", None)
                    self._version_store.update(rid, before_copy, tx)
                    # 恢复索引
                    after = self._version_store.read(rid, tx, self._tx_manager)
                    if after:
                        after_copy = dict(after)
                        after_copy.pop("rid", None)
                        self._index_manager.on_update(after_copy, before_copy, rid)

            elif op_type == "delete":
                # Undo delete: 重新插入 before_image
                before = op.get("before")
                if before:
                    before_copy = dict(before)
                    before_copy.pop("rid", None)
                    # 需要特殊处理：重新插入一个已删除的行
                    # 这里用 update 来恢复（因为 rid 还在版本链中，只是 tombstone）
                    self._version_store.update(rid, before_copy, tx)
                    self._index_manager.on_insert(before_copy, rid)

        # 截断 write_set
        tx.write_set = tx.write_set[:from_idx]

    # ---------- 非事务读（兼容旧接口） ----------

    def get_by_rid_nt(self, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """非事务读（用于无事务模式或恢复后）"""
        from .transaction import Transaction
        tx = Transaction(xid=0, start_ts=0)
        return self.get_by_rid(rid, tx, columns)

    def get_all_nt(self, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """非事务全表扫描"""
        from .transaction import Transaction
        tx = Transaction(xid=0, start_ts=0)
        return self.get_all(tx, columns)

    # ---------- 索引 ----------

    def create_index(self, column_name: str, index_type: IndexType = IndexType.ORDERED_ARRAY) -> bool:
        if column_name not in self._columns:
            raise ValueError(f"列 '{column_name}' 不存在")
        created = self._index_manager.create_index(column_name, index_type)
        if not created:
            return False
        # 回填现有数据
        index = self._index_manager.get_index(column_name)
        from .transaction import Transaction
        tx = Transaction(xid=0, start_ts=0)
        for rid in self._version_store.all_rids():
            data = self._version_store.read(rid, tx, None)
            if data and index is not None:
                index.insert(data.get(column_name), rid)
        return True

    def drop_index(self, column_name: str) -> bool:
        return self._index_manager.drop_index(column_name)

    def list_indices(self) -> List[str]:
        return self._index_manager.list_indices()

    def search_by_index(self, column_name: str, value: Any, tx: Transaction, columns: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
        rid_list = self._index_manager.search_eq(column_name, value)
        if rid_list is None:
            return None
        return [self.get_by_rid(rid, tx, columns) for rid in rid_list if self.get_by_rid(rid, tx) is not None]

    def search_range_by_index(self, column_name: str, min_value: Any = None, max_value: Any = None, tx: Transaction = None, columns: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
        rid_list = self._index_manager.search_range(column_name, min_value, max_value)
        if rid_list is None:
            return None
        if tx is None:
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
        return [self.get_by_rid(rid, tx, columns) for rid in rid_list if self.get_by_rid(rid, tx) is not None]

    # ---------- 列操作 ----------

    def add_column(self, column: str, default_value: Any = None) -> bool:
        if column in self._columns:
            return False
        self._columns.append(column)
        # 注意：版本存储不支持直接修改所有版本，这里简化处理
        # 新列在后续写入时自然包含
        return True

    def _add_column_to_all_rows(self, column: str, default_value: Any) -> None:
        if column not in self._columns:
            self._columns.append(column)

    def drop_column(self, column: str) -> bool:
        if column not in self._columns:
            return False
        self._index_manager.drop_index(column)
        self._columns.remove(column)
        return True

    def rename_column(self, old_name: str, new_name: str) -> bool:
        if old_name not in self._columns:
            return False
        if new_name in self._columns:
            raise ValueError(f"列 '{new_name}' 已存在")
        idx = self._columns.index(old_name)
        self._columns[idx] = new_name
        self._index_manager.rename_index(old_name, new_name)
        return True

    # ---------- 序列化 ----------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "columns": self._columns.copy(),
            "next_rid": self._version_store.get_next_rid(),
            "mode": self._mode.value,
            "indices": self._index_manager.to_dict(),
            "rows": self._version_store.to_dict(self._tx_manager),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MvccTable":
        mode = SchemaMode(d.get("mode", "strict"))
        table = cls(d["name"], d["columns"], mode)
        table._version_store.from_dict(d.get("rows", []), d.get("next_rid", 1))
        if "indices" in d:
            table._index_manager = IndexManager.from_dict(d["indices"])
        return table
