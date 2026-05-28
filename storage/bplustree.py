"""
bplustree.py - 内存版 B+树索引

为当前存储引擎提供可插拔的 B+树索引实现。
叶子节点保存 key -> [rid, ...]，并通过 next_leaf 支持范围扫描。
"""

import bisect
from typing import Any, Dict, List, Optional


class _BPlusNode:
    """B+树节点基类"""

    def __init__(self, is_leaf: bool):
        self.is_leaf = is_leaf
        self.keys: List[Any] = []


class _BPlusLeafNode(_BPlusNode):
    """叶子节点：保存 key -> rid 列表"""

    def __init__(self):
        super().__init__(is_leaf=True)
        self.values: List[List[int]] = []
        self.next_leaf: Optional["_BPlusLeafNode"] = None


class _BPlusInternalNode(_BPlusNode):
    """内部节点：保存分隔键和子指针"""

    def __init__(self):
        super().__init__(is_leaf=False)
        self.children: List[_BPlusNode] = []


class BPlusTreeIndex:
    """
    内存版 B+树索引

    说明：
    - 以叶子节点保存实际 rid 列表
    - 范围查询通过叶子链顺序扫描
    - 删除只移除 key/rid，不做合并和重平衡
    """

    def __init__(self, order: int = 32):
        if order < 3:
            raise ValueError("B+树 order 至少为 3")
        self.order = order
        self.root: _BPlusNode = _BPlusLeafNode()

    def insert(self, value: Any, rid: int) -> None:
        """插入一条索引记录"""
        path: List[_BPlusInternalNode] = []
        leaf = self._find_leaf(value, path)
        pos = bisect.bisect_left(leaf.keys, value)

        if pos < len(leaf.keys) and leaf.keys[pos] == value:
            rid_list = leaf.values[pos]
            rid_pos = bisect.bisect_left(rid_list, rid)
            if rid_pos >= len(rid_list) or rid_list[rid_pos] != rid:
                rid_list.insert(rid_pos, rid)
            return

        leaf.keys.insert(pos, value)
        leaf.values.insert(pos, [rid])

        if len(leaf.keys) > self.order:
            self._split_leaf(leaf, path)

    def delete(self, value: Any, rid: int) -> bool:
        """删除一条索引记录"""
        leaf = self._find_leaf(value)
        pos = bisect.bisect_left(leaf.keys, value)

        if pos >= len(leaf.keys) or leaf.keys[pos] != value:
            return False

        rid_list = leaf.values[pos]
        rid_pos = bisect.bisect_left(rid_list, rid)
        if rid_pos >= len(rid_list) or rid_list[rid_pos] != rid:
            return False

        rid_list.pop(rid_pos)
        if not rid_list:
            leaf.keys.pop(pos)
            leaf.values.pop(pos)

        return True

    def update(self, old_value: Any, new_value: Any, rid: int) -> None:
        """更新索引记录"""
        self.delete(old_value, rid)
        self.insert(new_value, rid)

    def search_eq(self, value: Any) -> List[int]:
        """等值查询"""
        leaf = self._find_leaf(value)
        pos = bisect.bisect_left(leaf.keys, value)
        if pos < len(leaf.keys) and leaf.keys[pos] == value:
            return leaf.values[pos].copy()
        return []

    def search_range(self, min_value: Any = None, max_value: Any = None) -> List[int]:
        """范围查询"""
        result: List[int] = []
        leaf = self._leftmost_leaf() if min_value is None else self._find_leaf(min_value)

        while leaf is not None:
            for key, rid_list in zip(leaf.keys, leaf.values):
                if min_value is not None and key < min_value:
                    continue
                if max_value is not None and key > max_value:
                    return result
                result.extend(rid_list)
            leaf = leaf.next_leaf

        return result

    def to_dict(self) -> Dict[str, Any]:
        """序列化索引结构"""
        return {
            "type": "bplus_tree",
            "order": self.order,
            "root": self._node_to_dict(self.root),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BPlusTreeIndex":
        """反序列化索引结构"""
        tree = cls(order=d.get("order", 32))
        tree.root = tree._node_from_dict(d["root"])
        tree._rebuild_leaf_links()
        return tree

    def _find_leaf(
        self,
        key: Any,
        path: Optional[List[_BPlusInternalNode]] = None,
    ) -> _BPlusLeafNode:
        """从根节点向下定位到目标叶子节点"""
        node = self.root
        while not node.is_leaf:
            internal = node
            assert isinstance(internal, _BPlusInternalNode)
            if path is not None:
                path.append(internal)
            child_pos = bisect.bisect_right(internal.keys, key)
            node = internal.children[child_pos]
        assert isinstance(node, _BPlusLeafNode)
        return node

    def _split_leaf(self, leaf: _BPlusLeafNode, path: List[_BPlusInternalNode]) -> None:
        """叶子分裂，并把分隔键插入父节点"""
        split_at = len(leaf.keys) // 2
        right = _BPlusLeafNode()
        right.keys = leaf.keys[split_at:]
        right.values = leaf.values[split_at:]
        right.next_leaf = leaf.next_leaf

        leaf.keys = leaf.keys[:split_at]
        leaf.values = leaf.values[:split_at]
        leaf.next_leaf = right

        self._insert_into_parent(leaf, right.keys[0], right, path)

    def _split_internal(self, node: _BPlusInternalNode, path: List[_BPlusInternalNode]) -> None:
        """内部节点分裂"""
        mid = len(node.keys) // 2
        promote_key = node.keys[mid]

        right = _BPlusInternalNode()
        right.keys = node.keys[mid + 1:]
        right.children = node.children[mid + 1:]

        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]

        self._insert_into_parent(node, promote_key, right, path)

    def _insert_into_parent(
        self,
        left: _BPlusNode,
        key: Any,
        right: _BPlusNode,
        path: List[_BPlusInternalNode],
    ) -> None:
        """把分裂得到的新子节点挂回父节点"""
        if not path:
            new_root = _BPlusInternalNode()
            new_root.keys = [key]
            new_root.children = [left, right]
            self.root = new_root
            return

        parent = path.pop()
        child_pos = parent.children.index(left)
        parent.keys.insert(child_pos, key)
        parent.children.insert(child_pos + 1, right)

        if len(parent.keys) > self.order:
            self._split_internal(parent, path)

    def _leftmost_leaf(self) -> _BPlusLeafNode:
        """获取最左侧叶子节点"""
        node = self.root
        while not node.is_leaf:
            internal = node
            assert isinstance(internal, _BPlusInternalNode)
            node = internal.children[0]
        assert isinstance(node, _BPlusLeafNode)
        return node

    def _node_to_dict(self, node: _BPlusNode) -> Dict[str, Any]:
        """递归序列化节点"""
        if node.is_leaf:
            leaf = node
            assert isinstance(leaf, _BPlusLeafNode)
            return {
                "is_leaf": True,
                "keys": leaf.keys.copy(),
                "values": [rid_list.copy() for rid_list in leaf.values],
            }

        internal = node
        assert isinstance(internal, _BPlusInternalNode)
        return {
            "is_leaf": False,
            "keys": internal.keys.copy(),
            "children": [self._node_to_dict(child) for child in internal.children],
        }

    def _node_from_dict(self, d: Dict[str, Any]) -> _BPlusNode:
        """递归反序列化节点"""
        if d["is_leaf"]:
            leaf = _BPlusLeafNode()
            leaf.keys = list(d.get("keys", []))
            leaf.values = [list(rid_list) for rid_list in d.get("values", [])]
            return leaf

        internal = _BPlusInternalNode()
        internal.keys = list(d.get("keys", []))
        internal.children = [self._node_from_dict(child) for child in d.get("children", [])]
        return internal

    def _rebuild_leaf_links(self) -> None:
        """反序列化后重建叶子链"""
        leaves: List[_BPlusLeafNode] = []
        self._collect_leaves(self.root, leaves)
        for i, leaf in enumerate(leaves):
            leaf.next_leaf = leaves[i + 1] if i + 1 < len(leaves) else None

    def _collect_leaves(self, node: _BPlusNode, leaves: List[_BPlusLeafNode]) -> None:
        """按从左到右顺序收集叶子节点"""
        if node.is_leaf:
            leaf = node
            assert isinstance(leaf, _BPlusLeafNode)
            leaves.append(leaf)
            return

        internal = node
        assert isinstance(internal, _BPlusInternalNode)
        for child in internal.children:
            self._collect_leaves(child, leaves)

    def __repr__(self) -> str:
        return f"BPlusTreeIndex(order={self.order})"
