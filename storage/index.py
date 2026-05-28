"""
index.py - 索引实现（角色C - 持久化与索引）

提供两种索引方式：
1. 有序数组 + 二分查找（基础实现）
2. B+树（进阶实现）

核心接口：
- Index.create_index() - 为列创建索引
- Index.search_eq() - 等值查询
- Index.search_range() - 范围查询
- Index.insert() - 维护索引
- Index.delete() - 维护索引
"""

import bisect
from typing import Any, Dict, List, Optional, Union
from enum import Enum
from .bplustree import BPlusTreeIndex


class IndexType(Enum):
    """索引类型"""
    ORDERED_ARRAY = "ordered_array"  # 有序数组 + 二分查找（基础）
    BPLUS_TREE = "bplus_tree"        # B+树（进阶）


class OrderedIndex:
    """
    有序数组索引（基础实现）
    
    使用有序列表存储 (value, rid) 对，支持二分查找。
    
    原理：
    - 维护有序列表 [(value1, rid1), (value2, rid2), ...]
    - 按 value 排序
    - 查询时用二分查找定位，时间复杂度 O(log N)
    
    适用场景：
    - 大多数查询场景
    - 不需要频繁范围查询
    
    限制：
    - 插入/删除时需要移位，O(N)
    - 可重复值处理为多条记录
    """
    
    def __init__(self):
        # 存储 (value, rid) 对，按 value 排序
        # 使用 list 而不是 set，以支持快速插入/删除和重复值
        self._entries: List[tuple] = []
    
    def insert(self, value: Any, rid: int) -> None:
        """
        插入一条索引记录
        
        Args:
            value: 列值
            rid: 行ID
        """
        # 使用 bisect 找到正确的插入位置
        pos = bisect.bisect_left(self._entries, (value, -1))
        self._entries.insert(pos, (value, rid))
    
    def delete(self, value: Any, rid: int) -> bool:
        """
        删除索引记录
        
        Args:
            value: 列值
            rid: 行ID
        
        Returns:
            是否找到并删除
        """
        try:
            self._entries.remove((value, rid))
            return True
        except ValueError:
            return False
    
    def search_eq(self, value: Any) -> List[int]:
        """
        等值查询 (value == target)
        
        Args:
            value: 查询值
        
        Returns:
            匹配的所有 rid 列表
        """
        # 二分查找左边界
        left = bisect.bisect_left(self._entries, (value, -1))
        # 二分查找右边界
        right = bisect.bisect_right(self._entries, (value, float('inf')))
        
        # 提取 rid
        return [rid for _, rid in self._entries[left:right]]
    
    def search_range(self, min_value: Any = None, max_value: Any = None) -> List[int]:
        """
        范围查询
        
        Args:
            min_value: 最小值（包含），None 表示无下限
            max_value: 最大值（包含），None 表示无上限
        
        Returns:
            在范围内的所有 rid
        """
        result = []
        for value, rid in self._entries:
            if min_value is not None and value < min_value:
                continue
            if max_value is not None and value > max_value:
                continue
            result.append(rid)
        return result
    
    def update(self, old_value: Any, new_value: Any, rid: int) -> None:
        """
        更新索引记录（用于字段更新）
        
        Args:
            old_value: 旧值
            new_value: 新值
            rid: 行ID
        """
        self.delete(old_value, rid)
        self.insert(new_value, rid)
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化索引"""
        return {
            'type': 'ordered_array',
            'entries': self._entries
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'OrderedIndex':
        """反序列化索引"""
        index = cls()
        index._entries = d.get('entries', [])
        return index
    
    def __repr__(self) -> str:
        return f"OrderedIndex(entries={len(self._entries)})"


class Index:
    """
    通用索引管理器
    
    为表的某一列创建和维护索引。
    """
    
    def __init__(self, column_name: str, index_type: IndexType = IndexType.ORDERED_ARRAY):
        """
        初始化索引
        
        Args:
            column_name: 列名
            index_type: 索引类型（默认有序数组）
        """
        self.column_name = column_name
        self.index_type = index_type
        
        if index_type == IndexType.ORDERED_ARRAY:
            self._index: Union[OrderedIndex, BPlusTreeIndex] = OrderedIndex()
        elif index_type == IndexType.BPLUS_TREE:
            self._index = BPlusTreeIndex()
        else:
            raise ValueError(f"不支持的索引类型: {index_type}")
    
    def insert(self, value: Any, rid: int) -> None:
        """插入索引记录"""
        self._index.insert(value, rid)
    
    def delete(self, value: Any, rid: int) -> bool:
        """删除索引记录"""
        return self._index.delete(value, rid)
    
    def search_eq(self, value: Any) -> List[int]:
        """
        等值查询（最常用）
        
        例如: SELECT * FROM users WHERE id = 123
        """
        return self._index.search_eq(value)
    
    def search_range(self, min_value: Any = None, max_value: Any = None) -> List[int]:
        """
        范围查询
        
        例如: SELECT * FROM users WHERE age >= 18 AND age <= 65
        """
        return self._index.search_range(min_value, max_value)
    
    def search_gt(self, value: Any) -> List[int]:
        """大于查询 (>) """
        return self._index.search_range(min_value=value)
    
    def search_lt(self, value: Any) -> List[int]:
        """小于查询 (<) """
        return self._index.search_range(max_value=value)
    
    def update(self, old_value: Any, new_value: Any, rid: int) -> None:
        """更新索引记录"""
        self._index.update(old_value, new_value, rid)
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化索引"""
        return {
            'column': self.column_name,
            'type': self.index_type.value,
            'index': self._index.to_dict()
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Index':
        """反序列化索引"""
        column = d['column']
        index_type = IndexType(d.get('type', 'ordered_array'))
        
        index = cls(column, index_type)
        if index_type == IndexType.ORDERED_ARRAY:
            index._index = OrderedIndex.from_dict(d['index'])
        elif index_type == IndexType.BPLUS_TREE:
            index._index = BPlusTreeIndex.from_dict(d['index'])
        else:
            raise ValueError(f"不支持的索引类型: {index_type}")
        return index
    
    def __repr__(self) -> str:
        return f"Index(column='{self.column_name}', type={self.index_type.value})"


class IndexManager:
    """
    索引管理器
    
    为表管理多个索引，在插入/删除/更新时自动维护所有索引。
    """
    
    def __init__(self):
        self._indices: Dict[str, Index] = {}  # column_name → Index
    
    def create_index(self, column_name: str, index_type: IndexType = IndexType.ORDERED_ARRAY) -> bool:
        """
        为列创建索引
        
        Args:
            column_name: 列名
            index_type: 索引类型
        
        Returns:
            是否成功创建（已存在返回 False）
        """
        if column_name in self._indices:
            return False
        
        self._indices[column_name] = Index(column_name, index_type)
        return True
    
    def has_index(self, column_name: str) -> bool:
        """检查列是否有索引"""
        return column_name in self._indices
    
    def drop_index(self, column_name: str) -> bool:
        """删除列的索引"""
        if column_name not in self._indices:
            return False
        del self._indices[column_name]
        return True
    
    def list_indices(self) -> List[str]:
        """列出所有有索引的列"""
        return list(self._indices.keys())

    def get_index(self, column_name: str) -> Optional[Index]:
        """获取指定列的索引对象"""
        return self._indices.get(column_name)

    def rename_index(self, old_name: str, new_name: str) -> bool:
        """重命名索引关联的列名"""
        if old_name not in self._indices:
            return False
        if new_name in self._indices:
            raise ValueError(f"列 '{new_name}' 已存在索引")

        index = self._indices.pop(old_name)
        index.column_name = new_name
        self._indices[new_name] = index
        return True
    
    def on_insert(self, row_data: Dict[str, Any], rid: int) -> None:
        """
        插入行时更新索引
        
        Args:
            row_data: 行数据 {column: value}
            rid: 行ID
        """
        for col_name, index in self._indices.items():
            if col_name in row_data:
                index.insert(row_data[col_name], rid)
    
    def on_delete(self, row_data: Dict[str, Any], rid: int) -> None:
        """
        删除行时更新索引
        
        Args:
            row_data: 行数据 {column: value}
            rid: 行ID
        """
        for col_name, index in self._indices.items():
            if col_name in row_data:
                index.delete(row_data[col_name], rid)
    
    def on_update(self, old_row_data: Dict[str, Any], new_row_data: Dict[str, Any], rid: int) -> None:
        """
        更新行时维护索引
        
        Args:
            old_row_data: 旧行数据
            new_row_data: 新行数据
            rid: 行ID
        """
        for col_name, index in self._indices.items():
            if col_name in new_row_data:
                old_val = old_row_data.get(col_name)
                new_val = new_row_data.get(col_name)
                
                # 值改变才更新索引
                if old_val != new_val:
                    index.update(old_val, new_val, rid)
    
    def search_eq(self, column_name: str, value: Any) -> Optional[List[int]]:
        """
        使用索引查询（等值）
        
        Returns:
            rid 列表，如果列不存在索引返回 None
        """
        if column_name not in self._indices:
            return None
        return self._indices[column_name].search_eq(value)
    
    def search_range(self, column_name: str, min_value: Any = None, max_value: Any = None) -> Optional[List[int]]:
        """
        使用索引查询（范围）
        
        Returns:
            rid 列表，如果列不存在索引返回 None
        """
        if column_name not in self._indices:
            return None
        return self._indices[column_name].search_range(min_value, max_value)
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化所有索引"""
        return {
            'indices': {
                col_name: index.to_dict()
                for col_name, index in self._indices.items()
            }
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'IndexManager':
        """反序列化索引"""
        manager = cls()
        for col_name, index_data in d.get('indices', {}).items():
            manager._indices[col_name] = Index.from_dict(index_data)
        return manager
    
    def __repr__(self) -> str:
        return f"IndexManager(indices={list(self._indices.keys())})"
