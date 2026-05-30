"""
mvcc_database.py - 支持事务的数据库

兼容现有 Database 接口，但所有写操作需要 Transaction 上下文
内部集成 TransactionManager + LockManager + WALManager
"""

from typing import Any, Dict, List, Optional

from .database import SchemaMode
from .mvcc_table import MvccTable
from .transaction import TransactionManager, Transaction, IsolationLevel, TxState
from .lock_manager import LockManager
from .wal import WALManager, WALRecordType


class MvccDatabase:
    """
    MVCC 数据库

    使用方式：
        db = MvccDatabase('mydb')
        tx = db.begin()
        db.insert(tx, 'users', {'id': 1, 'name': 'Alice'})
        db.commit(tx)
    """

    def __init__(self, name: str = "mydb", wal_path: str = "mydb2.wal"):
        self._name = name
        self._tables: Dict[str, MvccTable] = {}
        self._tx_manager = TransactionManager()
        self._lock_manager = LockManager()
        self._wal = WALManager(wal_path)

        # 将依赖注入到每个表
        self._inject_deps()

    def _inject_deps(self) -> None:
        """将锁管理器和 WAL 注入所有表"""
        for table in self._tables.values():
            table.set_lock_manager(self._lock_manager)
            table.set_wal(self._wal)
            table.set_tx_manager(self._tx_manager)

    # ---------- 事务控制 ----------

    def begin(self, isolation: IsolationLevel = IsolationLevel.REPEATABLE_READ) -> Transaction:
        """开始事务"""
        tx = self._tx_manager.begin(isolation)
        # 写 WAL BEGIN
        self._wal.append(
            xid=tx.xid,
            record_type=WALRecordType.BEGIN,
            table="",
            rid=0,
        )
        return tx

    def commit(self, tx: Transaction) -> bool:
        """提交事务"""
        if not tx.is_active():
            return False

        # 写 WAL COMMIT
        self._wal.append(
            xid=tx.xid,
            record_type=WALRecordType.COMMIT,
            table="",
            rid=0,
        )

        # 释放所有锁
        self._lock_manager.release_all(tx.xid)

        # 标记事务提交
        return self._tx_manager.commit(tx)

    def rollback(self, tx: Transaction) -> bool:
        """回滚事务"""
        if not tx.is_active():
            return False

        # 写 WAL ROLLBACK
        self._wal.append(
            xid=tx.xid,
            record_type=WALRecordType.ROLLBACK,
            table="",
            rid=0,
        )

        # 释放所有锁
        self._lock_manager.release_all(tx.xid)

        # 标记事务回滚
        return self._tx_manager.abort(tx)

    def savepoint(self, tx: Transaction, name: str) -> None:
        """设置保存点"""
        self._tx_manager.set_savepoint(tx, name)

    def rollback_to_savepoint(self, tx: Transaction, name: str) -> bool:
        """回滚到保存点"""
        if name not in tx.savepoints:
            return False
        idx = tx.savepoints[name]
        # 对每个受影响的表执行 undo
        for table in self._tables.values():
            table.undo_operations(tx, idx)
        # 清理保存点记录
        self._tx_manager.rollback_to_savepoint(tx, name)
        return True

    # ---------- 表管理 ----------

    def create_table(self, table_name: str, columns: List[str], mode: SchemaMode = SchemaMode.STRICT) -> MvccTable:
        if table_name in self._tables:
            raise ValueError(f"表 '{table_name}' 已存在")
        table = MvccTable(table_name, columns, mode)
        table.set_lock_manager(self._lock_manager)
        table.set_wal(self._wal)
        table.set_tx_manager(self._tx_manager)
        self._tables[table_name] = table
        return table

    def get_table(self, table_name: str) -> Optional[MvccTable]:
        return self._tables.get(table_name)

    def drop_table(self, table_name: str) -> bool:
        if table_name not in self._tables:
            return False
        del self._tables[table_name]
        return True

    def list_tables(self) -> List[str]:
        return list(self._tables.keys())

    def has_table(self, table_name: str) -> bool:
        return table_name in self._tables

    # ---------- 数据操作（需要 Transaction） ----------

    def insert(self, tx: Transaction, table_name: str, data: Dict[str, Any]) -> int:
        """事务内插入"""
        table = self._get_table_or_raise(table_name)
        return table.insert(data, tx)

    def select_all(self, tx: Transaction, table_name: str, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """事务内全表扫描"""
        table = self._get_table_or_raise(table_name)
        return table.get_all(tx, columns)

    def select_by_index(self, tx: Transaction, table_name: str, index: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """事务内按索引查询"""
        table = self._get_table_or_raise(table_name)
        return table.get_by_index(index, tx, columns)

    def get_by_rid(self, tx: Transaction, table_name: str, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """事务内按 rid 查询"""
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.get_by_rid(rid, tx, columns)

    def update(self, tx: Transaction, table_name: str, rid: int, new_data: Dict[str, Any]) -> bool:
        """事务内更新"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.update_by_rid(rid, new_data, tx)

    def delete(self, tx: Transaction, table_name: str, rid: int) -> bool:
        """事务内删除"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.delete_by_rid(rid, tx)

    # ---------- 非事务读（兼容旧接口） ----------

    def select_all_nt(self, table_name: str, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """非事务全表扫描"""
        table = self._get_table_or_raise(table_name)
        return table.get_all_nt(columns)

    def get_by_rid_nt(self, table_name: str, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """非事务按 rid 查询"""
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.get_by_rid_nt(rid, columns)

    # ---------- 索引 ----------

    def create_index(self, table_name: str, column_name: str, index_type) -> bool:
        table = self._get_table_or_raise(table_name)
        return table.create_index(column_name, index_type)

    def drop_index(self, table_name: str, column_name: str) -> bool:
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.drop_index(column_name)

    def search_by_index(self, tx: Transaction, table_name: str, column_name: str, value: Any, columns: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.search_by_index(column_name, value, tx, columns)

    def search_range_by_index(self, tx: Transaction, table_name: str, column_name: str, min_value: Any = None, max_value: Any = None, columns: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.search_range_by_index(column_name, min_value, max_value, tx, columns)

    # ---------- 列操作 ----------

    def add_column(self, table_name: str, column: str, default_value: Any = None) -> bool:
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.add_column(column, default_value)

    def drop_column(self, table_name: str, column: str) -> bool:
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.drop_column(column)

    def rename_column(self, table_name: str, old_name: str, new_name: str) -> bool:
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.rename_column(old_name, new_name)

    # ---------- 内部方法 ----------

    def _get_table_or_raise(self, table_name: str) -> MvccTable:
        table = self._tables.get(table_name)
        if table is None:
            raise ValueError(f"表 '{table_name}' 不存在")
        return table

    # ---------- 序列化 ----------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "tables": {
                name: table.to_dict()
                for name, table in self._tables.items()
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], wal_path: str = "mydb2.wal") -> "MvccDatabase":
        db = cls(d["name"], wal_path)
        for name, table_dict in d.get("tables", {}).items():
            db._tables[name] = MvccTable.from_dict(table_dict)
        db._inject_deps()
        return db

    # ---------- 调试 ----------

    def get_lock_info(self) -> Dict[str, Any]:
        return self._lock_manager.get_lock_info()

    def get_active_transactions(self) -> List[Transaction]:
        return self._tx_manager.active_transactions()
