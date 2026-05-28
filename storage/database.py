"""
database.py - 核心数据结构实现（优化版）

优化点：
1. 增强查询：支持按索引访问、指定列查看
2. 严格/宽松模式：灵活更新，自动扩列
3. 列操作：添加/删除列
4. 封装性：明确 private/public，通过接口访问
"""

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


class Table:
    """
    数据表实现（优化版）
    
    封装性：
    - _name, _columns, _rows, _next_rid, _mode: 私有属性
    - 通过 property 提供只读访问
    - 修改必须通过接口方法
    
    新模式：
    - STRICT: 严格模式，未知列报错
    - LOOSE: 宽松模式，未知列自动扩列
    """
    
    def __init__(self, name: str, columns: List[str], mode: SchemaMode = SchemaMode.STRICT):
        self._name = name
        self._columns = columns.copy()
        self._rows: List[Row] = []
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
        return len(self._rows)
    
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
        self._rows.append(row)
        
        assigned_rid = self._next_rid


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
        if index < 0 or index >= len(self._rows):
            return None
        return self._rows[index].to_dict(columns)
    
    def get_by_rid(self, rid: int, columns: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        根据 RowID 获取行（优化点1：支持指定列）
        
        Args:
            rid: 行号
            columns: 指定列，None 表示所有列
        """
        for row in self._rows:
            if row.rid == rid:
                return row.to_dict(columns)
        return None
    
    def get_all(self, columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        获取所有行（优化点1：支持指定列）
        
        Args:
            columns: 指定列，None 表示所有列
        """
        return [row.to_dict(columns) for row in self._rows]

    def create_index(self, column_name: str, index_type: IndexType = IndexType.ORDERED_ARRAY) -> bool:
        """为指定列创建索引，并回填现有数据"""
        if column_name not in self._columns:
            raise ValueError(f"列 '{column_name}' 不存在")

        created = self._index_manager.create_index(column_name, index_type)
        if not created:
            return False

        index = self._index_manager.get_index(column_name)
        for row in self._rows:
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
        if index < 0 or index >= len(self._rows):
            return False
        row = self._rows.pop(index)
        self._index_manager.on_delete(row._data, row.rid)
        return True
    
    def delete_by_rid(self, rid: int) -> bool:
        """根据 RowID 删除行"""
        for i, row in enumerate(self._rows):
            if row.rid == rid:
                self._index_manager.on_delete(row._data, row.rid)
                self._rows.pop(i)
                return True
        return False
    
    def update_by_rid(self, rid: int, new_data: Dict[str, Any]) -> bool:
        """
        根据 RowID 更新行（优化点2：灵活更新）
        
        - 只更新存在的列
        - 未知列：严格模式忽略，宽松模式扩列
        """
        row = None
        for r in self._rows:
            if r.rid == rid:
                row = r
                break
        
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

        row.update(valid_data)
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
        for row in self._rows:
            row.set(column, default_value)
        return True
    
    def _add_column_to_all_rows(self, column: str, default_value: Any) -> None:
        """内部方法：添加列到 schema 和所有行"""
        if column not in self._columns:
            self._columns.append(column)
            for row in self._rows:
                row.set(column, default_value)
    
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
        for row in self._rows:
            row.delete_column(column)
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
        
        for row in self._rows:
            if row.has_column(old_name):
                value = row.get(old_name)
                row.delete_column(old_name)
                row.set(new_name, value)
        return True

    def _rows_from_rids(
        self,
        rid_list: List[int],
        columns: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """按 rid 顺序返回对应行"""
        row_map = {row.rid: row for row in self._rows}
        return [
            row_map[rid].to_dict(columns)
            for rid in rid_list
            if rid in row_map
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
                for row in self._rows
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
            table._rows.append(row)
        if 'indices' in d:
            table._index_manager = IndexManager.from_dict(d['indices'])
        return table
    
    def __repr__(self) -> str:
        return f"Table(name='{self._name}', columns={self._columns}, rows={len(self._rows)}, mode={self._mode.value})"


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
