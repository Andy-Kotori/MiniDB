"""
database.py - 核心数据结构实现（优化版）

优化点：
1. 增强查询：支持按索引访问、指定列查看
2. 严格/宽松模式：灵活更新，自动扩列
3. 列操作：添加/删除列
4. 封装性：明确 private/public，通过接口访问
"""

import pickle
from typing import Any, Dict, List, Optional, Iterator, Set, Union
from enum import Enum
from .index import IndexManager, IndexType


class SchemaMode(Enum):
    """表的模式：严格模式或宽松模式"""
    STRICT = "strict"    # 严格模式：未知列报错
    LOOSE = "loose"      # 宽松模式：未知列自动扩列


class Row:
    """
    单行数据封装（优化版）
    
    封装性：
    - _rid: 私有，只读属性
    - _data: 私有，通过 get/set/update 访问
    """
    
    def __init__(self, rid: int, data: Dict[str, Any]):
        self._rid = rid
        self._data = data.copy()
    
    @property
    def rid(self) -> int:
        """行号，只读"""
        return self._rid
    
    def get(self, column: str) -> Any:
        """获取指定列的值"""
        return self._data.get(column)
    
    def set(self, column: str, value: Any) -> None:
        """设置单列值"""
        self._data[column] = value
    
    def update(self, new_data: Dict[str, Any]) -> None:
        """更新多列"""
        self._data.update(new_data)
    
    def has_column(self, column: str) -> bool:
        """检查是否有某列"""
        return column in self._data
    
    def delete_column(self, column: str) -> bool:
        """删除某一列"""
        if column in self._data:
            del self._data[column]
            return True
        return False
    
    def to_dict(self, columns: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        转换为字典格式
        
        Args:
            columns: 指定列，None 表示所有列
        
        Returns:
            包含 rid 和指定列数据的字典
        """
        result = {'rid': self._rid}
        if columns is None:
            result.update(self._data)
        else:
            for col in columns:
                result[col] = self._data.get(col)
        return result
    
    def __repr__(self) -> str:
        return f"Row(rid={self._rid}, data={self._data})"


class DataPage:
    """内存中的数据页，用于承载多条记录"""

    PAGE_SIZE = 4096
    PAGE_OVERHEAD = 128
    SLOT_OVERHEAD = 16

    def __init__(self, page_id: int):
        self.page_id = page_id
        self._rows: List[Row] = []
        self._rid_to_slot: Dict[int, int] = {}
        self._used_bytes = self.PAGE_OVERHEAD

    @classmethod
    def _row_size(cls, row: Row) -> int:
        """估算单行在页中的大小"""
        payload = {'rid': row.rid, 'data': row._data}
        return len(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)) + cls.SLOT_OVERHEAD

    def row_count(self) -> int:
        """页内行数"""
        return len(self._rows)

    def can_fit(self, row: Row) -> bool:
        """检查页内是否能容纳新行"""
        return self._used_bytes + self._row_size(row) <= self.PAGE_SIZE

    def insert_row(self, row: Row) -> bool:
        """插入行到当前页"""
        if not self.can_fit(row):
            return False

        self._rows.append(row)
        self._rid_to_slot[row.rid] = len(self._rows) - 1
        self._used_bytes += self._row_size(row)
        return True

    def get_row(self, rid: int) -> Optional[Row]:
        """按 rid 获取页内行"""
        slot = self._rid_to_slot.get(rid)
        if slot is None:
            return None
        return self._rows[slot]

    def remove_row(self, rid: int) -> Optional[Row]:
        """从页中删除一行"""
        slot = self._rid_to_slot.pop(rid, None)
        if slot is None:
            return None

        row = self._rows.pop(slot)
        self._used_bytes -= self._row_size(row)

        for i in range(slot, len(self._rows)):
            self._rid_to_slot[self._rows[i].rid] = i

        return row

    def can_replace_row(self, rid: int, new_row: Row) -> bool:
        """检查替换现有行后是否仍能放入当前页"""
        current_row = self.get_row(rid)
        if current_row is None:
            return False

        current_size = self._row_size(current_row)
        new_size = self._row_size(new_row)
        return self._used_bytes - current_size + new_size <= self.PAGE_SIZE

    def replace_row(self, rid: int, new_row: Row) -> bool:
        """替换页内现有行"""
        slot = self._rid_to_slot.get(rid)
        if slot is None or not self.can_replace_row(rid, new_row):
            return False

        old_row = self._rows[slot]
        self._used_bytes -= self._row_size(old_row)
        self._rows[slot] = new_row
        self._used_bytes += self._row_size(new_row)
        self._rid_to_slot[new_row.rid] = slot
        return True

    def iter_rows(self) -> Iterator[Row]:
        """顺序遍历页内行"""
        return iter(self._rows)


class Table:
    """
    数据表实现（优化版）
    
    封装性：
    - _name, _columns, _pages, _next_rid, _mode: 私有属性
    - 通过 property 提供只读访问
    - 修改必须通过接口方法
    
    新模式：
    - STRICT: 严格模式，未知列报错
    - LOOSE: 宽松模式，未知列自动扩列
    """
    
    def __init__(self, name: str, columns: List[str], mode: SchemaMode = SchemaMode.STRICT):
        self._name = name
        self._columns = columns.copy()
        self._pages: List[DataPage] = []
        self._rid_to_page: Dict[int, DataPage] = {}
        self._row_order: List[int] = []
        self._row_count = 0
        self._next_rid = 1
        self._mode = mode
        self._index_manager = IndexManager()
    
    # ========== 只读属性（外部可读，不可直接修改） ==========
    
    @property
    def name(self) -> str:
        """表名"""
        return self._name
    
    @property
    def columns(self) -> List[str]:
        """列名列表（拷贝，防止外部修改）"""
        return self._columns.copy()
    
    @property
    def row_count(self) -> int:
        """行数"""
        return self._row_count

    @property
    def page_count(self) -> int:
        """页数"""
        return len(self._pages)
    
    @property
    def next_rid(self) -> int:
        """下一个可用 rid"""
        return self._next_rid
    
    @property
    def mode(self) -> SchemaMode:
        """当前模式"""
        return self._mode
    
    def set_mode(self, mode: SchemaMode) -> None:
        """设置模式（对外接口）"""
        self._mode = mode
    
    # ========== 行操作 ==========
    
    def insert(self, data: Dict[str, Any]) -> int:
        """
        插入一行数据（优化版）
        
        严格模式：未知列报错
        宽松模式：未知列自动添加到 schema，并为所有已有行添加空值
        """
        input_cols = set(data.keys())
        existing_cols = set(self._columns)
        unknown_cols = input_cols - existing_cols
        
        if unknown_cols:
            if self._mode == SchemaMode.STRICT:
                raise ValueError(f"未知列: {unknown_cols}，表 '{self._name}' 的列为: {self._columns}")
            else:
                # 宽松模式：扩列
                for col in unknown_cols:
                    self._add_column_to_all_rows(col, None)
        
        # 构建完整数据（缺失列设为 None）
        full_data = {col: data.get(col) for col in self._columns}
        
        row = Row(self._next_rid, full_data)
        assigned_rid = self._next_rid
        self._store_row(row)


        # 维护索引
        self._index_manager.on_insert(full_data, assigned_rid)
        self._next_rid += 1
        
        return assigned_rid
    
    def get_by_index(self, index: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        按索引（第几行）获取行（优化点1）
        
        Args:
            index: 行索引（从0开始）
            columns: 指定列，None 表示所有列
        
        Returns:
            行字典，索引越界返回 None
        """
        if index < 0 or index >= len(self._row_order):
            return None
        row = self._get_row(self._row_order[index])
        return None if row is None else row.to_dict(columns)
    
    def get_by_rid(self, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        根据 RowID 获取行（优化点1：支持指定列）
        
        Args:
            rid: 行号
            columns: 指定列，None 表示所有列
        """
        row = self._get_row(rid)
        return None if row is None else row.to_dict(columns)
    
    def get_all(self, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        获取所有行（优化点1：支持指定列）
        
        Args:
            columns: 指定列，None 表示所有列
        """
        return [row.to_dict(columns) for row in self._iter_rows()]

    def create_index(self, column_name: str, index_type: IndexType = IndexType.ORDERED_ARRAY) -> bool:
        """为指定列创建索引，并回填现有数据"""
        if column_name not in self._columns:
            raise ValueError(f"列 '{column_name}' 不存在")

        created = self._index_manager.create_index(column_name, index_type)
        if not created:
            return False

        index = self._index_manager.get_index(column_name)
        for row in self._iter_rows():
            if index is not None:
                index.insert(row.get(column_name), row.rid)
        return True

    def drop_index(self, column_name: str) -> bool:
        """删除指定列上的索引"""
        return self._index_manager.drop_index(column_name)

    def list_indices(self) -> List[str]:
        """列出已建索引的列"""
        return self._index_manager.list_indices()

    def search_by_index(
        self,
        column_name: str,
        value: Any,
        columns: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """使用索引进行等值查询"""
        rid_list = self._index_manager.search_eq(column_name, value)
        if rid_list is None:
            return None
        return self._rows_from_rids(rid_list, columns)

    def search_range_by_index(
        self,
        column_name: str,
        min_value: Any = None,
        max_value: Any = None,
        columns: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """使用索引进行范围查询"""
        rid_list = self._index_manager.search_range(column_name, min_value, max_value)
        if rid_list is None:
            return None
        return self._rows_from_rids(rid_list, columns)
    
    def delete_by_index(self, index: int) -> bool:
        """
        按索引删除行
        
        Args:
            index: 行索引（从0开始）
        """
        if index < 0 or index >= len(self._row_order):
            return False
        rid = self._row_order[index]
        return self.delete_by_rid(rid)
    
    def delete_by_rid(self, rid: int) -> bool:
        """根据 RowID 删除行"""
        row = self._get_row(rid)
        if row is None:
            return False

        self._index_manager.on_delete(row._data, row.rid)
        self._remove_row(rid)
        return True
    
    def update_by_rid(self, rid: int, new_data: Dict[str, Any]) -> bool:
        """
        根据 RowID 更新行（优化点2：灵活更新）
        
        - 只更新存在的列
        - 未知列：严格模式忽略，宽松模式扩列
        """
        row = self._get_row(rid)
        if row is None:
            return False
        
        input_cols = set(new_data.keys())
        existing_cols = set(self._columns)
        unknown_cols = input_cols - existing_cols
        
        if unknown_cols and self._mode == SchemaMode.LOOSE:
            # 宽松模式：扩列
            for col in unknown_cols:
                self._add_column_to_all_rows(col, None)
        


        # 只更新已存在的列（严格模式下未知列被静默忽略）
        valid_data = {k: v for k, v in new_data.items() if k in self._columns}

        # 维护索引
        old_data = row._data.copy()
        self._index_manager.on_update(old_data, valid_data, rid)
        updated_row = Row(rid, old_data)
        updated_row.update(valid_data)
        self._replace_row(updated_row)
        return True
    
    # ========== 列操作（优化点3） ==========
    
    def add_column(self, column: str, default_value: Any = None) -> bool:
        """
        为表添加一列
        
        Args:
            column: 列名
            default_value: 默认值
        
        Returns:
            是否成功添加（已存在返回 False）
        """
        if column in self._columns:
            return False
        
        self._columns.append(column)
        for row in self._iter_rows():
            row.set(column, default_value)
        self._repack_pages()
        return True
    
    def _add_column_to_all_rows(self, column: str, default_value: Any) -> None:
        """内部方法：添加列到 schema 和所有行"""
        if column not in self._columns:
            self._columns.append(column)
            for row in self._iter_rows():
                row.set(column, default_value)
            self._repack_pages()
    
    def drop_column(self, column: str) -> bool:
        """
        删除表中的一列
        
        Args:
            column: 列名
        
        Returns:
            是否成功删除
        """
        if column not in self._columns:
            return False
        
        self._index_manager.drop_index(column)
        self._columns.remove(column)
        for row in self._iter_rows():
            row.delete_column(column)
        self._repack_pages()
        return True
    
    def rename_column(self, old_name: str, new_name: str) -> bool:
        """
        重命名列
        
        Args:
            old_name: 原列名
            new_name: 新列名
        """
        if old_name not in self._columns:
            return False
        if new_name in self._columns:
            raise ValueError(f"列 '{new_name}' 已存在")
        
        idx = self._columns.index(old_name)
        self._columns[idx] = new_name
        self._index_manager.rename_index(old_name, new_name)
        
        for row in self._iter_rows():
            if row.has_column(old_name):
                value = row.get(old_name)
                row.delete_column(old_name)
                row.set(new_name, value)
        self._repack_pages()
        return True

    def _iter_rows(self) -> Iterator[Row]:
        """按逻辑顺序遍历表中所有行"""
        for rid in self._row_order:
            row = self._get_row(rid)
            if row is not None:
                yield row

    def _get_row(self, rid: int) -> Optional[Row]:
        """按 rid 获取运行时行对象"""
        page = self._rid_to_page.get(rid)
        if page is None:
            return None
        return page.get_row(rid)

    def _store_row(self, row: Row, track_order: bool = True) -> None:
        """把一行放入某个可容纳的数据页"""
        for page in self._pages:
            if page.insert_row(row):
                self._rid_to_page[row.rid] = page
                self._row_count += 1
                if track_order:
                    self._row_order.append(row.rid)
                return

        new_page = DataPage(len(self._pages))
        if not new_page.insert_row(row):
            raise ValueError("单行数据过大，无法放入数据页")

        self._pages.append(new_page)
        self._rid_to_page[row.rid] = new_page
        self._row_count += 1
        if track_order:
            self._row_order.append(row.rid)

    def _remove_row(self, rid: int) -> Optional[Row]:
        """从页中移除一行，并维护行顺序"""
        page = self._rid_to_page.pop(rid, None)
        if page is None:
            return None

        row = page.remove_row(rid)
        if row is not None:
            self._row_count -= 1
            self._row_order.remove(rid)
        return row

    def _replace_row(self, new_row: Row) -> None:
        """替换一行，如当前页放不下则迁移到其他页"""
        rid = new_row.rid
        page = self._rid_to_page.get(rid)
        if page is None:
            raise ValueError(f"rid={rid} 不存在")

        if page.replace_row(rid, new_row):
            return

        page.remove_row(rid)
        del self._rid_to_page[rid]
        self._row_count -= 1
        self._store_row(new_row, track_order=False)

    def _repack_pages(self) -> None:
        """对全表重新分页，处理 schema 变更后的页大小变化"""
        rows = [Row(row.rid, row._data) for row in self._iter_rows()]
        row_order = self._row_order.copy()

        self._pages = []
        self._rid_to_page = {}
        self._row_count = 0

        row_map = {row.rid: row for row in rows}
        for rid in row_order:
            self._store_row(row_map[rid], track_order=False)

        self._row_order = row_order

    def _rows_from_rids(
        self,
        rid_list: List[int],
        columns: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """按 rid 顺序返回对应行"""
        return [
            self._get_row(rid).to_dict(columns)
            for rid in rid_list
            if self._get_row(rid) is not None
        ]
    
    # ========== 序列化 ==========
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            'name': self._name,
            'columns': self._columns,
            'next_rid': self._next_rid,
            'mode': self._mode.value,
            'indices': self._index_manager.to_dict(),
            'rows': [
                {'rid': row.rid, 'data': row._data}
                for row in self._iter_rows()
            ]
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Table':
        """从字典反序列化"""
        mode = SchemaMode(d.get('mode', 'strict'))
        table = cls(d['name'], d['columns'], mode)
        table._next_rid = d['next_rid']
        for row_dict in d['rows']:
            row = Row(row_dict['rid'], row_dict['data'])
            table._store_row(row)
        if 'indices' in d:
            table._index_manager = IndexManager.from_dict(d['indices'])
        return table
    
    def __repr__(self) -> str:
        return f"Table(name='{self._name}', columns={self._columns}, rows={self._row_count}, pages={len(self._pages)}, mode={self._mode.value})"


class Database:
    """
    数据库实现（优化版）
    
    封装性：
    - _name, _tables: 私有属性
    - 通过接口方法操作表
    """
    
    def __init__(self, name: str = "mydb"):
        self._name = name
        self._tables: Dict[str, Table] = {}
    
    # ========== 只读属性 ==========
    
    @property
    def name(self) -> str:
        """数据库名"""
        return self._name
    
    # ========== 表管理 ==========
    
    def create_table(self, table_name: str, columns: List[str], mode: SchemaMode = SchemaMode.STRICT) -> Table:
        """
        创建新表
        
        Args:
            table_name: 表名
            columns: 列名列表
            mode: 模式（严格/宽松）
        """
        if table_name in self._tables:
            raise ValueError(f"表 '{table_name}' 已存在")
        
        table = Table(table_name, columns, mode)
        self._tables[table_name] = table
        return table
    
    def get_table(self, table_name: str) -> Optional[Table]:
        """
        获取表对象
        
        返回 Table 实例，可以通过它调用 Table 的所有方法
        """
        return self._tables.get(table_name)
    
    def drop_table(self, table_name: str) -> bool:
        """删除表"""
        if table_name not in self._tables:
            return False
        del self._tables[table_name]
        return True
    
    def list_tables(self) -> List[str]:
        """列出所有表名"""
        return list(self._tables.keys())
    
    def has_table(self, table_name: str) -> bool:
        """检查表是否存在"""
        return table_name in self._tables
    
    # ========== 快捷操作（通过 Database 直接操作数据） ==========
    
    def insert(self, table_name: str, data: Dict[str, Any]) -> int:
        """向指定表插入数据"""
        table = self._get_table_or_raise(table_name)
        return table.insert(data)
    
    def select_all(self, table_name: str, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """查询表中所有数据（优化：支持指定列）"""
        table = self._get_table_or_raise(table_name)
        return table.get_all(columns)
    
    def select_by_index(self, table_name: str, index: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """按索引查询（优化：支持指定列）"""
        table = self._get_table_or_raise(table_name)
        return table.get_by_index(index, columns)
    
    def get_by_rid(self, table_name: str, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """按 RowID 查询（优化：支持指定列）"""
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.get_by_rid(rid, columns)
    
    def update(self, table_name: str, rid: int, new_data: Dict[str, Any]) -> bool:
        """按 RowID 更新行"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.update_by_rid(rid, new_data)
    
    def delete(self, table_name: str, rid: int) -> bool:
        """按 RowID 删除行"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.delete_by_rid(rid)
    
    def delete_by_index(self, table_name: str, index: int) -> bool:
        """按索引删除行"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.delete_by_index(index)

    def create_index(
        self,
        table_name: str,
        column_name: str,
        index_type: IndexType = IndexType.ORDERED_ARRAY,
    ) -> bool:
        """为表的指定列创建索引"""
        table = self._get_table_or_raise(table_name)
        return table.create_index(column_name, index_type)

    def drop_index(self, table_name: str, column_name: str) -> bool:
        """删除表的指定索引"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.drop_index(column_name)

    def search_by_index(
        self,
        table_name: str,
        column_name: str,
        value: Any,
        columns: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """使用索引进行等值查询"""
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.search_by_index(column_name, value, columns)

    def search_range_by_index(
        self,
        table_name: str,
        column_name: str,
        min_value: Any = None,
        max_value: Any = None,
        columns: Optional[List[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """使用索引进行范围查询"""
        table = self._tables.get(table_name)
        if table is None:
            return None
        return table.search_range_by_index(column_name, min_value, max_value, columns)
    
    # ========== 列操作快捷方式 ==========
    
    def add_column(self, table_name: str, column: str, default_value: Any = None) -> bool:
        """为表添加列"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.add_column(column, default_value)
    
    def drop_column(self, table_name: str, column: str) -> bool:
        """删除表的列"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.drop_column(column)
    
    def rename_column(self, table_name: str, old_name: str, new_name: str) -> bool:
        """重命名列"""
        table = self._tables.get(table_name)
        if table is None:
            return False
        return table.rename_column(old_name, new_name)
    
    # ========== 内部方法 ==========
    
    def _get_table_or_raise(self, table_name: str) -> Table:
        """获取表，不存在则报错"""
        table = self._tables.get(table_name)
        if table is None:
            raise ValueError(f"表 '{table_name}' 不存在")
        return table
    
    # ========== 序列化 ==========
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            'name': self._name,
            'tables': {
                name: table.to_dict()
                for name, table in self._tables.items()
            }
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Database':
        """从字典反序列化"""
        db = cls(d['name'])
        for name, table_dict in d['tables'].items():
            db._tables[name] = Table.from_dict(table_dict)
        return db
    
    def __repr__(self) -> str:
        return f"Database(name='{self._name}', tables={list(self._tables.keys())})"
