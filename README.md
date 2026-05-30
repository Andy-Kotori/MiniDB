# MiniDB 2.0 — 支持 MVCC + 事务的迷你数据库

> 基于纯 Python 实现的教学级数据库系统，支持 SQL 交互、MVCC 多版本并发控制、行级锁、预写日志（WAL）和崩溃恢复。

---

## 项目结构

```
mydb2/
├── main.py                      # 程序入口，启动 REPL
├── storage_adapter.py           # 前后端适配器（支持普通/MVCC 双模式）
├── frontend/
│   ├── __init__.py
│   ├── repl.py                  # 命令行交互、SQL 解析、事务命令
│   └── where_parser.py          # WHERE 条件解析器
├── storage/
│   ├── __init__.py              # 模块导出
│   ├── database.py              # 原始 Database / Table / Row（无事务）
│   ├── mvcc_database.py         # MVCC 数据库（MvccDatabase）
│   ├── mvcc_table.py            # MVCC 数据表（MvccTable）
│   ├── transaction.py           # 事务管理器（TransactionManager）
│   ├── lock_manager.py          # 行级锁管理器（LockManager）
│   ├── version_store.py         # MVCC 版本链存储（VersionStore）
│   ├── wal.py                   # 预写日志管理器（WALManager）
│   ├── index.py                 # 索引管理器 + 有序数组索引
│   ├── bplustree.py             # B+ 树索引实现
│   ├── persistence.py           # 持久化（binary / pickle / json）
│   └── page_manager.py          # 磁盘页管理器（4KB 页）
├── minidb.data                  # 默认数据文件（JSON 格式）
├── mydb2.wal                    # WAL 日志文件（二进制格式）
└── .minidb_history              # REPL 命令历史
```

---

## 快速开始

### 安装依赖

```bash
cd mydb2
pip install sqlparse prompt_toolkit
```

### 启动交互式 REPL（MVCC 模式）

```bash
python main.py --mvcc
```

```
MiniDB started. Type '.exit' to quit, '.help' for help.
minidb> CREATE TABLE users (id INT, name VARCHAR(50), age INT)
Table 'users' created with columns: ['id', 'name', 'age']
minidb> INSERT INTO users VALUES (1, 'Alice', 20)
Inserted into 'users': [1, 'Alice', 20]
minidb> SELECT * FROM users
+----+-------+-----+
| id | name  | age |
+----+-------+-----+
| 1  | Alice | 20  |
+----+-------+-----+
```

### 单条 SQL 执行

```bash
python main.py --mvcc -e "CREATE TABLE t (id INT, name VARCHAR(50))"
python main.py --mvcc -e "INSERT INTO t VALUES (1, 'hello')"
python main.py --mvcc -e "SELECT * FROM t"
```

---

## 支持的 SQL 语句

| 语句 | 示例 | 说明 |
|------|------|------|
| `CREATE TABLE` | `CREATE TABLE users (id INT, name VARCHAR(50))` | 创建表 |
| `INSERT INTO` | `INSERT INTO users VALUES (1, 'Alice')` | 插入数据 |
| `SELECT` | `SELECT * FROM users WHERE age > 20` | 查询（支持 WHERE、指定列） |
| `UPDATE` | `UPDATE users SET age = 30 WHERE id = 1` | 条件更新（支持多列） |
| `DELETE` | `DELETE FROM users WHERE id = 1` | 条件删除 |
| `DROP TABLE` | `DROP TABLE users` | 删除表 |

---

## 事务命令

| 命令 | 说明 |
|------|------|
| `.begin` | 开始事务（默认隔离级别：REPEATABLE READ） |
| `.commit` | 提交事务 |
| `.rollback` | 回滚事务 |
| `.savepoint <name>` | 设置保存点 |
| `.rollback_to <name>` | 回滚到指定保存点 |
| `.tx` | 查看当前事务状态 |
| `.locks` | 查看当前锁状态 |

### 事务示例

```
minidb> .begin
Transaction started (xid=1, isolation=REPEATABLE READ)
minidb> INSERT INTO users VALUES (2, 'Bob', 25)
minidb> SELECT * FROM users
+----+-------+-----+
| id | name  | age |
+----+-------+-----+
| 1  | Alice | 20  |
| 2  | Bob   | 25  |
+----+-------+-----+
minidb> .rollback
Transaction rolled back (xid=1)
minidb> SELECT * FROM users
+----+-------+-----+
| id | name  | age |
+----+-------+-----+
| 1  | Alice | 20  |
+----+-------+-----+
```

### 条件查询与更新示例

```
minidb> SELECT * FROM users WHERE age > 20
+----+---------+-----+
| id | name    | age |
+----+---------+-----+
| 2  | Bob     | 25  |
| 3  | Charlie | 30  |
+----+---------+-----+

minidb> SELECT name, age FROM users WHERE id = 1
+-------+-----+
| name  | age |
+-------+-----+
| Alice | 20  |
+-------+-----+

minidb> UPDATE users SET age = 21 WHERE id = 1
Updated 1 row(s).

minidb> DELETE FROM users WHERE age > 25
Deleted 1 row(s).
```

---

## 其他点命令

| 命令 | 说明 |
|------|------|
| `.exit` | 退出并保存数据 |
| `.help` | 显示帮助 |
| `.tables` | 列出所有表 |
| `.schema <表名>` | 查看表结构 |

---

## 核心特性

### 1. MVCC（多版本并发控制）

- **读不阻塞读**：多个事务可以同时读取同一行
- **读不阻塞写**：读操作不会阻止写操作
- **写只锁单行**：写操作仅锁定目标行，不影响其他行

### 2. 行级锁

- **共享锁（S）**：用于读操作
- **排他锁（X）**：用于写操作
- **锁升级**：支持 S → X 升级
- **死锁检测**：等待图算法自动检测死锁环
- **锁超时**：默认 5 秒超时

### 3. 隔离级别

| 隔离级别 | 脏读 | 不可重复读 | 幻读 |
|---------|------|-----------|------|
| READ UNCOMMITTED | ✅ 允许 | ✅ 允许 | ✅ 允许 |
| READ COMMITTED | ❌ 禁止 | ✅ 允许 | ✅ 允许 |
| REPEATABLE READ（默认）| ❌ 禁止 | ❌ 禁止 | ✅ 允许 |
| SERIALIZABLE | ❌ 禁止 | ❌ 禁止 | ❌ 禁止 |

### 4. WAL（预写日志）

- 所有修改先写日志，再写数据
- 日志文件：`mydb2.wal`
- 支持崩溃恢复（Redo + Undo）

### 5. 崩溃恢复

启动时自动检查 WAL 文件：
1. 找到最后一个 CHECKPOINT
2. **Redo**：重放所有已提交事务的操作
3. **Undo**：回滚所有未提交事务的操作

---

## Python API

### 基本使用（自动事务）

```python
from storage_adapter import StorageAdapter

storage = StorageAdapter(use_mvcc=True)
storage.create_table('users', ['id', 'name', 'age'])
storage.insert('users', [1, 'Alice', 20])

result = storage.select_all('users')
print(result)
# {'columns': ['id', 'name', 'age'], 'rows': [[1, 'Alice', 20]]}
```

### 显式事务

```python
from storage_adapter import StorageAdapter

storage = StorageAdapter(use_mvcc=True)
storage.create_table('users', ['id', 'name', 'age'])

# 开始事务
storage.begin()
storage.insert('users', [1, 'Alice', 20])
storage.insert('users', [2, 'Bob', 25])

# 回滚（数据不会保存）
storage.rollback()

# 或者提交
# storage.commit()
```

### 底层 API（直接使用 MvccDatabase）

```python
from storage import MvccDatabase, IsolationLevel

db = MvccDatabase('mydb', wal_path='mydb2.wal')
db.create_table('users', ['id', 'name', 'age'])

# 显式事务
tx = db.begin(IsolationLevel.REPEATABLE_READ)
db.insert(tx, 'users', {'id': 1, 'name': 'Alice', 'age': 20})
db.commit(tx)

# 保存点
tx = db.begin()
db.insert(tx, 'users', {'id': 2, 'name': 'Bob', 'age': 25})
db.savepoint(tx, 'sp1')
db.insert(tx, 'users', {'id': 3, 'name': 'Charlie', 'age': 30})
db.rollback_to_savepoint(tx, 'sp1')  # Charlie 被撤销
db.commit(tx)
```

---

## 架构说明

### 数据流

```
SQL 输入
    │
    ▼
frontend/repl.py  ──SQL 解析──┐
                              ▼
              storage_adapter.py（适配器）
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
普通模式      MVCC 事务模式      持久化
(Database)   (MvccDatabase)    (JSON/WAL)
    │               │
    ▼               ▼
Table          MvccTable
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
VersionStore  LockManager  WALManager
(MVCC版本链)   (行级锁)     (预写日志)
```

### 版本链（MVCC 核心）

```
rid=1 的版本链：

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  RowVersion     │     │  RowVersion     │     │  RowVersion     │
│  created_by=3   │────▶│  created_by=2   │────▶│  created_by=1   │
│  data={'age':21}│     │  data={'age':20}│     │  data={'age':18}│
│  expired_by=0   │     │  expired_by=3   │     │  expired_by=2   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
       ↑（最新）
       │
  事务3 更新 age=21
  （未提交时只有事务3可见）
```

---

## 与 mydb（v1.0）的对比

| 特性 | mydb (v1.0) | mydb2 (v2.0) |
|------|-------------|--------------|
| SQL 解析 | ❌ | ✅ sqlparse |
| 交互式 REPL | ❌ | ✅ prompt_toolkit |
| 内存分页 | ❌ | ✅ DataPage (4KB) |
| B+ 树索引 | ❌ | ✅ |
| 二进制持久化 | ❌ | ✅ 页式格式 |
| **MVCC** | ❌ | ✅ |
| **行级锁** | ❌ | ✅ |
| **事务** | ❌ | ✅ BEGIN/COMMIT/ROLLBACK |
| **保存点** | ❌ | ✅ SAVEPOINT |
| **隔离级别** | ❌ | ✅ 4 级 |
| **WAL** | ❌ | ✅ |
| **崩溃恢复** | ❌ | ✅ Redo + Undo |

---

## 已知限制

1. **WHERE 限制**：支持 `=, !=, >, <, >=, <=` 和 `AND`，不支持 `OR`, `NOT`, `LIKE`, `IN`
2. **JOIN**：暂不支持多表连接
3. **ALTER TABLE**：仅支持列的增删改，不支持复杂表结构变更
4. **网络访问**：仅支持本地嵌入式使用
5. **大事务内存**：MVCC 版本链在事务期间保留旧版本，大事务可能消耗较多内存

---

## 测试

```bash
cd mydb2

# 运行所有模块的导入测试
python -c "from storage import *; print('All imports OK')"

# 运行综合集成测试
python -c "
from storage import MvccDatabase, IsolationLevel

db = MvccDatabase('test')
db.create_table('users', ['id', 'name'])

tx = db.begin()
db.insert(tx, 'users', {'id': 1, 'name': 'Alice'})
db.commit(tx)

tx = db.begin()
rows = db.select_all(tx, 'users')
print(rows)
db.commit(tx)
"
```

---

## License

学习项目，自由使用。
