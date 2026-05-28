# 额外优化建议

## 已完成的优化

### ✅ 优化点1: 增强查询功能
- `get_by_index(index)` - 按第几行查询
- `get_by_rid(rid, columns)` - 支持指定列
- `get_all(columns)` - 支持指定列返回

### ✅ 优化点2: 严格/宽松模式
- `SchemaMode.STRICT` - 未知列报错
- `SchemaMode.LOOSE` - 未知列自动扩列
- 灵活更新：只改存在的列，忽略未知列（严格模式）或扩列（宽松模式）

### ✅ 优化点3: 列操作
- `add_column(col, default)` - 添加列
- `drop_column(col)` - 删除列
- `rename_column(old, new)` - 重命名列

### ✅ 优化点4: 封装性
- 所有属性私有化（`_name`, `_columns` 等）
- 通过 `@property` 提供只读访问
- 修改必须通过接口方法

---

## 建议的进一步优化方向

### 1. 查询条件支持（WHERE 子句基础）

当前只支持全表扫描和按 rid/index 查询。可以添加简单的条件过滤：

```python
# 按条件查询（为角色A的 WHERE 子句做准备）
table.select_where(lambda row: row['age'] > 18)
table.select_where(lambda row: row['name'] == 'Alice')

# 或更简单的形式
db.select_where('users', {'age': 20})  # age == 20
db.select_where('users', {'age': ('>', 18)})  # age > 18
```

**价值**：角色A解析 `WHERE age > 18` 后可以调用此接口，无需自己实现过滤逻辑。

---

### 2. 批量操作

当前插入/更新都是单条操作，可以添加批量接口提升性能：

```python
# 批量插入
table.insert_many([
    {'id': 1, 'name': 'Alice'},
    {'id': 2, 'name': 'Bob'},
    {'id': 3, 'name': 'Charlie'},
])

# 批量更新
table.update_many(
    condition=lambda row: row['department'] == '技术部',
    new_data={'salary': lambda old: old * 1.1}  # 技术部涨薪10%
)
```

**价值**：减少循环开销，为以后的角色C批量写入页做准备。

---

### 3. 数据类型系统

当前列没有类型信息，可以添加简单的类型检查：

```python
from enum import Enum

class DataType(Enum):
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    STRING = "STRING"
    BOOLEAN = "BOOLEAN"

# 创建表时指定类型
db.create_table('users', {
    'id': DataType.INTEGER,
    'name': DataType.STRING,
    'age': DataType.INTEGER,
    'salary': DataType.FLOAT,
})

# 插入时自动检查/转换类型
table.insert({'id': '123', 'name': 456})  # 尝试转换，失败则报错
```

**价值**：
- 与 SQL 类型系统对应（`CREATE TABLE users (id INT, name VARCHAR(255))`）
- 提前发现类型错误

---

### 4. 事务支持（简化版）

虽然完整事务很复杂，但可以添加简单的批量操作原子性：

```python
# 批量操作要么全成功，要么全回滚
with db.transaction() as txn:
    txn.insert('accounts', {'id': 1, 'balance': 100})
    txn.update('accounts', 2, {'balance': 200})  # 假设这行失败
    # 自动回滚，第一条插入也被撤销
```

**简化实现**：
- 在内存中保存操作日志
- 失败时反向执行恢复
- 不涉及磁盘 WAL（那是角色C的工作）

**价值**：保证批量数据的一致性。

---

### 5. 视图（View）机制

可以添加简单的视图支持，将查询条件封装：

```python
# 创建视图：技术部员工
db.create_view('tech_employees', 
    lambda: db.select_where('employees', {'department': '技术部'}))

# 使用视图
db.query_view('tech_employees')  # 返回技术部员工列表
```

**价值**：角色A可以实现 `CREATE VIEW` 语句。

---

### 6. 导入/导出（CSV/JSON）

当前持久化是二进制格式，可以添加文本格式导入导出：

```python
# 导出为 CSV
Persistence.export_csv(db, 'users', 'users.csv')

# 从 CSV 导入
table = Persistence.import_csv('users.csv', has_header=True)

# JSON 行格式（每行一个JSON对象）
Persistence.export_jsonl(db, 'users', 'users.jsonl')
```

**价值**：方便与其他工具（Excel、Pandas）交换数据。

---

### 7. 统计信息

为查询优化（角色C）提供基础统计数据：

```python
table.stats()  # 返回统计信息
# {
#     'row_count': 10000,
#     'column_stats': {
#         'age': {'min': 18, 'max': 65, 'distinct': 48},
#         'department': {'distinct': 5, 'most_common': '技术部'}
#     }
# }
```

**价值**：角色C可以用这些统计信息决定是否为某列创建索引。

---

### 8. 内存使用优化

当前 Row 用 dict 存储，对于大量数据可以优化：

```python
# 方案A：使用 __slots__ 减少内存
class Row:
    __slots__ = ['_rid', '_data']  # 阻止动态属性，减少内存

# 方案B：列式存储（为角色C分页做准备）
class ColumnStoreTable:
    def __init__(self):
        self._columns = {}
        # _columns['id'] = [1, 2, 3, 4, 5]  # 整列存储
        # _columns['name'] = ['Alice', 'Bob', ...]
```

**价值**：当前是行式存储（方便理解），但列式存储对 OLAP 查询更高效。

---

## 优先级建议

| 优先级 | 优化项 | 理由 |
|-------|--------|------|
| 🔴 高 | 查询条件支持 | 角色A需要 WHERE 支持 |
| 🔴 高 | 数据类型系统 | SQL 标准需要 |
| 🟡 中 | 批量操作 | 性能优化 |
| 🟡 中 | 导入/导出 | 实用功能 |
| 🟢 低 | 事务支持 | 复杂度较高 |
| 🟢 低 | 视图机制 | 锦上添花 |
| 🔵 未来 | 统计信息 | 角色C需要 |
| 🔵 未来 | 列式存储 | 架构级改动 |

---

## 与角色A/C的对接建议

### 与角色A（前端）

建议角色A在 SQL 解析后，生成如下结构调用角色B：

```python
# CREATE TABLE
sql: CREATE TABLE users (id INT, name VARCHAR(50))
db.create_table('users', ['id', 'name'])  # 暂时忽略类型，或传给角色B校验

# INSERT
sql: INSERT INTO users VALUES (1, 'Alice')
db.insert('users', {'id': 1, 'name': 'Alice'})

# SELECT * FROM users WHERE age > 18
rows = db.select_all('users')  # 先全表扫描
result = [r for r in rows if r['age'] > 18]  # 角色A过滤，或调用角色B的条件查询

# SELECT name, age FROM users
rows = db.select_all('users', columns=['name', 'age'])
```

### 与角色C（索引）

建议接口契约：

```python
# 角色C创建索引时
index.build([row.rid for row in table.get_all()])

# 角色C查询后
rids = index.find('age', 20)  # 返回 [rid1, rid2, ...]
for rid in rids:
    row = table.get_by_rid(rid)  # 通过角色B获取数据
```

**关键点**：
- 角色B保证 rid 稳定不重复
- 角色C只存储 rid，不存储实际数据
- 数据获取必须通过角色B接口

---

## 总结

当前代码已覆盖核心功能，建议下一步：
1. **立即**：与角色A协商 SQL 解析后的调用接口
2. **短期**：实现查询条件支持（WHERE 基础）
3. **中期**：添加数据类型系统
4. **长期**：与角色C对接索引接口
