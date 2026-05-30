"""
where_parser.py - WHERE 条件解析器

支持：
- 比较操作：=, !=, >, <, >=, <=
- 逻辑连接：AND（多个条件同时满足）
- 暂不支持：OR, NOT, 括号嵌套, LIKE, IN, 函数
"""

from typing import Any, Dict, List, Optional
from sqlparse.sql import Where, Comparison
from sqlparse.tokens import Token


class Condition:
    """单个条件"""

    def __init__(self, column: str, operator: str, value: Any):
        self.column = column
        self.operator = operator
        self.value = value

    def __repr__(self):
        return f"Condition({self.column} {self.operator} {self.value!r})"


class WhereParser:
    """WHERE 条件解析器"""

    # 支持的操作符
    OPERATORS = {"=", "!=", ">", "<", ">=", "<="}

    def parse(self, where_token: Where) -> List[Condition]:
        """
        解析 Where token 为条件列表
        多个条件之间是 AND 关系
        """
        conditions = []

        for token in where_token.tokens:
            if isinstance(token, Comparison):
                cond = self._parse_comparison(token)
                if cond:
                    conditions.append(cond)

        return conditions

    def _parse_comparison(self, comp: Comparison) -> Optional[Condition]:
        """解析单个 Comparison token"""
        # 提取非空白部分
        parts = [t for t in comp.tokens if not t.is_whitespace]
        if len(parts) != 3:
            return None

        left, op, right = parts

        # 列名
        column = self._extract_identifier(left)
        if not column:
            return None

        # 操作符
        operator = op.value
        if operator not in self.OPERATORS:
            return None

        # 值
        value = self._parse_value(right)

        return Condition(column, operator, value)

    def _extract_identifier(self, token) -> Optional[str]:
        """提取标识符（列名）"""
        val = token.value.strip('"\'`')
        return val

    def _parse_value(self, token) -> Any:
        """解析值为 Python 类型"""
        val = token.value.strip()

        # NULL
        if val.upper() == "NULL":
            return None

        # 字符串（单引号或双引号）
        if (val.startswith("'") and val.endswith("'")) or (
            val.startswith('"') and val.endswith('"')
        ):
            return val[1:-1]

        # 布尔值
        if val.upper() == "TRUE":
            return True
        if val.upper() == "FALSE":
            return False

        # 数字
        try:
            return int(val)
        except ValueError:
            try:
                return float(val)
            except ValueError:
                return val

    def evaluate(self, row: Dict[str, Any], conditions: List[Condition]) -> bool:
        """
        判断一行是否满足所有条件（AND 关系）
        """
        for cond in conditions:
            if not self._evaluate_single(row, cond):
                return False
        return True

    def _evaluate_single(self, row: Dict[str, Any], cond: Condition) -> bool:
        """评估单个条件"""
        actual = row.get(cond.column)
        expected = cond.value

        # 处理 None
        if actual is None and expected is None:
            return cond.operator == "="
        if actual is None or expected is None:
            return False

        try:
            if cond.operator == "=":
                return actual == expected
            elif cond.operator == "!=":
                return actual != expected
            elif cond.operator == ">":
                return actual > expected
            elif cond.operator == "<":
                return actual < expected
            elif cond.operator == ">=":
                return actual >= expected
            elif cond.operator == "<=":
                return actual <= expected
        except TypeError:
            # 类型不匹配（如字符串和数字比较）
            return False

        return False

    def find_index_condition(self, conditions: List[Condition], indexed_columns: List[str]) -> Optional[Condition]:
        """
        在条件列表中找到一个可以使用索引的等值条件
        返回该条件，如果没有则返回 None
        """
        for cond in conditions:
            if cond.operator == "=" and cond.column in indexed_columns:
                return cond
        return None
