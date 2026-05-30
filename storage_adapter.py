"""
storage_adapter.py - 适配器：将 Database 适配为前端需要的 Storage 接口

支持两种模式：
1. 普通模式：使用原有的 Database（无事务）
2. 事务模式：使用 MvccDatabase（支持 BEGIN/COMMIT/ROLLBACK）
"""

from typing import Dict, List, Any, Optional

from storage.database import Database, SchemaMode
from storage.mvcc_database import MvccDatabase
from storage.transaction import Transaction, IsolationLevel


class StorageAdapter:
    """适配器类，将 Database 适配为前端需要的 Storage 接口"""

    def __init__(self, db=None, use_mvcc: bool = False):
        if db is not None:
            self.db = db
            self._use_mvcc = isinstance(db, MvccDatabase)
        elif use_mvcc:
            self.db = MvccDatabase()
            self._use_mvcc = True
        else:
            self.db = Database()
            self._use_mvcc = False

        self._current_tx: Optional[Transaction] = None

    # ---------- 模式切换 ----------

    @property
    def use_mvcc(self) -> bool:
        return self._use_mvcc

    def enable_mvcc(self, wal_path: str = "mydb2.wal") -> None:
        """启用 MVCC 模式（会丢失当前数据！）"""
        if not self._use_mvcc:
            self.db = MvccDatabase(wal_path=wal_path)
            self._use_mvcc = True
            self._current_tx = None

    # ---------- 事务接口 ----------

    def in_transaction(self) -> bool:
        return self._current_tx is not None and self._current_tx.is_active()

    def begin(self, isolation: str = "REPEATABLE READ") -> None:
        """开始事务"""
        if not self._use_mvcc:
            raise RuntimeError("MVCC mode is not enabled. Use enable_mvcc() first.")
        if self.in_transaction():
            raise RuntimeError("Transaction already in progress")

        level = IsolationLevel(isolation)
        self._current_tx = self.db.begin(level)
        print(f"Transaction started (xid={self._current_tx.xid}, isolation={isolation})")

    def commit(self) -> None:
        """提交事务"""
        if not self.in_transaction():
            raise RuntimeError("No active transaction")
        self.db.commit(self._current_tx)
        print(f"Transaction committed (xid={self._current_tx.xid})")
        self._current_tx = None

    def rollback(self) -> None:
        """回滚事务"""
        if not self.in_transaction():
            raise RuntimeError("No active transaction")
        self.db.rollback(self._current_tx)
        print(f"Transaction rolled back (xid={self._current_tx.xid})")
        self._current_tx = None

    def savepoint(self, name: str) -> None:
        """设置保存点"""
        if not self.in_transaction():
            raise RuntimeError("No active transaction")
        self.db.savepoint(self._current_tx, name)
        print(f"Savepoint '{name}' set")

    def rollback_to_savepoint(self, name: str) -> None:
        """回滚到保存点"""
        if not self.in_transaction():
            raise RuntimeError("No active transaction")
        self.db.rollback_to_savepoint(self._current_tx, name)
        print(f"Rolled back to savepoint '{name}'")

    # ---------- 表管理 ----------

    def create_table(self, table_name: str, columns: List[str]) -> None:
        """创建表（使用严格模式）"""
        if self._use_mvcc and self.in_transaction():
            # MVCC + 事务内：表操作不在事务内（简化）
            self.db.create_table(table_name, columns, SchemaMode.STRICT)
        elif self._use_mvcc:
            self.db.create_table(table_name, columns, SchemaMode.STRICT)
        else:
            self.db.create_table(table_name, columns, SchemaMode.STRICT)

    def get_table_names(self) -> List[str]:
        return self.db.list_tables()

    def get_schema(self, table_name: str) -> Optional[List[str]]:
        table = self.db.get_table(table_name)
        if table is None:
            return None
        return table.columns

    # ---------- 数据操作 ----------

    def insert(self, table_name: str, values: List[Any]) -> None:
        """插入数据：将列表转换为字典"""
        table = self.db.get_table(table_name)
        if table is None:
            raise ValueError(f"Table '{table_name}' does not exist")

        columns = table.columns
        if len(values) != len(columns):
            raise ValueError(f"Expected {len(columns)} values, got {len(values)}")

        data = dict(zip(columns, values))

        if self._use_mvcc and self.in_transaction():
            self.db.insert(self._current_tx, table_name, data)
        elif self._use_mvcc:
            # 自动包裹为单语句事务
            tx = self.db.begin()
            try:
                self.db.insert(tx, table_name, data)
                self.db.commit(tx)
            except Exception:
                self.db.rollback(tx)
                raise
        else:
            self.db.insert(table_name, data)

    def select_all(self, table_name: str) -> Dict[str, Any]:
        """查询所有数据，返回前端需要的格式"""
        if self._use_mvcc and self.in_transaction():
            rows = self.db.select_all(self._current_tx, table_name)
        elif self._use_mvcc:
            tx = self.db.begin()
            try:
                rows = self.db.select_all(tx, table_name)
                self.db.commit(tx)
            except Exception:
                self.db.rollback(tx)
                raise
        else:
            rows = self.db.select_all(table_name)

        table = self.db.get_table(table_name)
        columns = table.columns if table else []

        # 去掉 rid 字段，只保留列值
        data_rows = []
        for row in rows:
            row_values = [row.get(col) for col in columns]
            data_rows.append(row_values)

        return {
            'columns': columns,
            'rows': data_rows
        }

    def update(self, table_name: str, rid: int, new_data: Dict[str, Any]) -> bool:
        """更新行（MVCC模式）"""
        if not self._use_mvcc:
            raise RuntimeError("Update requires MVCC mode")

        if self.in_transaction():
            return self.db.update(self._current_tx, table_name, rid, new_data)
        else:
            tx = self.db.begin()
            try:
                result = self.db.update(tx, table_name, rid, new_data)
                self.db.commit(tx)
                return result
            except Exception:
                self.db.rollback(tx)
                raise

    def delete(self, table_name: str, rid: int) -> bool:
        """删除行（MVCC模式）"""
        if not self._use_mvcc:
            raise RuntimeError("Delete requires MVCC mode")

        if self.in_transaction():
            return self.db.delete(self._current_tx, table_name, rid)
        else:
            tx = self.db.begin()
            try:
                result = self.db.delete(tx, table_name, rid)
                self.db.commit(tx)
                return result
            except Exception:
                self.db.rollback(tx)
                raise

    # ---------- 条件查询/更新/删除 ----------

    def select_where(self, table_name: str, columns: Optional[List[str]],
                     predicate) -> Dict[str, Any]:
        """
        条件查询
        predicate: 函数，接收 row_dict 返回 bool
        """
        if self._use_mvcc and self.in_transaction():
            rows = self.db.select_all(self._current_tx, table_name)
        elif self._use_mvcc:
            tx = self.db.begin()
            try:
                rows = self.db.select_all(tx, table_name)
                self.db.commit(tx)
            except Exception:
                self.db.rollback(tx)
                raise
        else:
            rows = self.db.select_all(table_name)

        # 过滤
        filtered = [r for r in rows if predicate(r)]

        # 选择列
        table = self.db.get_table(table_name)
        result_columns = table.columns if table else []
        if columns is not None:
            result_columns = [c for c in columns if c in result_columns]

        data_rows = []
        for row in filtered:
            if columns is None:
                row_values = [row.get(col) for col in result_columns]
            else:
                row_values = [row.get(col) for col in columns]
            data_rows.append(row_values)

        return {
            'columns': result_columns if columns is None else columns,
            'rows': data_rows
        }

    def update_where(self, table_name: str, predicate, new_data: Dict[str, Any]) -> int:
        """
        条件更新
        返回影响行数
        """
        if not self._use_mvcc:
            raise RuntimeError("update_where requires MVCC mode")

        # 先查询所有行
        if self.in_transaction():
            rows = self.db.select_all(self._current_tx, table_name)
        else:
            tx = self.db.begin()
            try:
                rows = self.db.select_all(tx, table_name)
                self.db.commit(tx)
            except Exception:
                self.db.rollback(tx)
                raise

        # 找到匹配的行
        matched = [r for r in rows if predicate(r)]

        # 更新每一行
        count = 0
        for row in matched:
            rid = row['rid']
            # 合并新旧数据
            updated = dict(row)
            updated.update(new_data)
            updated.pop('rid', None)

            if self.in_transaction():
                if self.db.update(self._current_tx, table_name, rid, updated):
                    count += 1
            else:
                tx = self.db.begin()
                try:
                    if self.db.update(tx, table_name, rid, updated):
                        count += 1
                    self.db.commit(tx)
                except Exception:
                    self.db.rollback(tx)
                    raise

        return count

    def delete_where(self, table_name: str, predicate) -> int:
        """
        条件删除
        返回影响行数
        """
        if not self._use_mvcc:
            raise RuntimeError("delete_where requires MVCC mode")

        # 先查询所有行
        if self.in_transaction():
            rows = self.db.select_all(self._current_tx, table_name)
        else:
            tx = self.db.begin()
            try:
                rows = self.db.select_all(tx, table_name)
                self.db.commit(tx)
            except Exception:
                self.db.rollback(tx)
                raise

        # 找到匹配的行
        matched = [r for r in rows if predicate(r)]

        # 删除每一行
        count = 0
        for row in matched:
            rid = row['rid']
            if self.in_transaction():
                if self.db.delete(self._current_tx, table_name, rid):
                    count += 1
            else:
                tx = self.db.begin()
                try:
                    if self.db.delete(tx, table_name, rid):
                        count += 1
                    self.db.commit(tx)
                except Exception:
                    self.db.rollback(tx)
                    raise

        return count

    def drop_table(self, table_name: str) -> bool:
        """删除表"""
        return self.db.drop_table(table_name)

    # ---------- 导入/导出 ----------

    def to_dict(self) -> Dict[str, Any]:
        return self.db.to_dict()

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        if self._use_mvcc:
            self.db = MvccDatabase.from_dict(data)
        else:
            self.db = Database.from_dict(data)
