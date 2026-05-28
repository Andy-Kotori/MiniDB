#!/usr/bin/env python3
"""
test_storage.py - Storage 模块测试（优化版）

测试覆盖：
    - 基础 CRUD 操作
    - 优化点1: 按索引查询、指定列查询
    - 优化点2: 严格/宽松模式、灵活更新
    - 优化点3: 列操作（添加/删除列）
    - 优化点4: 封装性（property 访问）
    - 持久化

运行方式:
    python test_storage.py          # 运行测试
    python test_storage.py --demo   # 交互式演示
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import Database, Table, Row, SchemaMode, Persistence, IndexType


class TestRunner:
    """测试运行器"""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
    
    def test(self, name: str, condition: bool, msg: str = ""):
        if condition:
            print(f"  ✓ {name}")
            self.passed += 1
        else:
            print(f"  ✗ {name}: {msg}")
            self.failed += 1
    
    def run_all(self):
        print("=" * 60)
        print("Storage 模块测试（优化版）")
        print("=" * 60)
        
        self.test_row()
        self.test_table_basic()
        self.test_query_enhancement()      # 优化点1
        self.test_schema_mode()            # 优化点2
        self.test_column_operations()      # 优化点3
        self.test_encapsulation()          # 优化点4
        self.test_indexing()
        self.test_database()
        self.test_persistence()
        self.test_integration()
        
        print("\n" + "=" * 60)
        print(f"测试结果: 通过 {self.passed}, 失败 {self.failed}")
        print("=" * 60)
        
        return self.failed == 0
    
    def test_row(self):
        """测试 Row 类"""
        print("\n▶ Row 测试")
        
        row = Row(1, {'id': 1, 'name': 'Alice', 'age': 20})
        
        self.test("rid 属性", row.rid == 1)
        self.test("get 方法", row.get('name') == 'Alice')
        
        row.set('age', 21)
        self.test("set 方法", row.get('age') == 21)
        
        row.update({'name': 'Bob', 'age': 22})
        self.test("update 方法", row.get('name') == 'Bob' and row.get('age') == 22)
        
        self.test("has_column", row.has_column('name') and not row.has_column('unknown'))
        
        # 指定列输出
        partial = row.to_dict(['name'])
        self.test("to_dict 指定列", partial == {'rid': 1, 'name': 'Bob'})
        
        # 删除列
        row.delete_column('age')
        self.test("delete_column", not row.has_column('age'))
    
    def test_table_basic(self):
        """测试 Table 基础功能"""
        print("\n▶ Table 基础测试")
        
        table = Table('users', ['id', 'name'], mode=SchemaMode.STRICT)
        
        # 插入
        rid1 = table.insert({'id': 1, 'name': 'Alice'})
        rid2 = table.insert({'id': 2, 'name': 'Bob'})
        
        self.test("插入返回 rid", rid1 == 1 and rid2 == 2)
        self.test("row_count 属性", table.row_count == 2)
        
        # 查询
        all_rows = table.get_all()
        self.test("get_all", len(all_rows) == 2)
        
        row = table.get_by_rid(1)
        self.test("get_by_rid", row is not None and row['name'] == 'Alice')
        
        # 删除
        self.test("delete_by_rid", table.delete_by_rid(1) and table.row_count == 1)
        
        # 删除后 rid 不重复
        rid3 = table.insert({'id': 3, 'name': 'Charlie'})
        self.test("rid 不重复利用", rid3 == 3)
    
    def test_query_enhancement(self):
        """测试优化点1: 增强查询（按索引、指定列）"""
        print("\n▶ 增强查询测试（优化点1）")
        
        table = Table('products', ['id', 'name', 'price', 'category'])
        table.insert({'id': 1, 'name': 'Apple', 'price': 5.0, 'category': 'fruit'})
        table.insert({'id': 2, 'name': 'Banana', 'price': 3.0, 'category': 'fruit'})
        table.insert({'id': 3, 'name': 'Carrot', 'price': 2.0, 'category': 'vegetable'})
        
        # 按索引查询
        row = table.get_by_index(0)
        self.test("get_by_index(0)", row is not None and row['name'] == 'Apple')
        
        row = table.get_by_index(1)
        self.test("get_by_index(1)", row is not None and row['name'] == 'Banana')
        
        self.test("get_by_index(越界)", table.get_by_index(10) is None)
        
        # 按索引 + 指定列
        row = table.get_by_index(0, columns=['name', 'price'])
        self.test("get_by_index 指定列", 
                 row == {'rid': 1, 'name': 'Apple', 'price': 5.0} and 'category' not in row)
        
        # get_by_rid + 指定列
        row = table.get_by_rid(2, columns=['name'])
        self.test("get_by_rid 指定列", row == {'rid': 2, 'name': 'Banana'})
        
        # get_all + 指定列
        rows = table.get_all(columns=['id', 'name'])
        self.test("get_all 指定列", 
                 len(rows) == 3 and all('price' not in r for r in rows))
        
        # 按索引删除
        self.test("delete_by_index", table.delete_by_index(1) and table.row_count == 2)
        # 删除后，原来的 index 2 变成 index 1
        row = table.get_by_index(1)
        self.test("delete_by_index 后索引变化", row['name'] == 'Carrot')
    
    def test_schema_mode(self):
        """测试优化点2: 严格/宽松模式"""
        print("\n▶ SchemaMode 测试（优化点2）")
        
        # 严格模式
        strict_table = Table('strict_t', ['id', 'name'], mode=SchemaMode.STRICT)
        self.test("strict 模式属性", strict_table.mode == SchemaMode.STRICT)
        
        try:
            strict_table.insert({'id': 1, 'name': 'Alice', 'unknown_col': 'value'})
            self.test("strict 模式未知列报错", False)
        except ValueError:
            self.test("strict 模式未知列报错", True)
        
        # 宽松模式
        loose_table = Table('loose_t', ['id', 'name'], mode=SchemaMode.LOOSE)
        self.test("loose 模式属性", loose_table.mode == SchemaMode.LOOSE)
        
        # 宽松模式插入：自动扩列
        loose_table.insert({'id': 1, 'name': 'Alice'})
        loose_table.insert({'id': 2, 'name': 'Bob', 'age': 25})  # 新列
        
        self.test("loose 模式自动扩列", 'age' in loose_table.columns)
        self.test("loose 模式旧行补空", loose_table.get_by_index(0)['age'] is None)
        self.test("loose 模式新行有值", loose_table.get_by_index(1)['age'] == 25)
        
        # 灵活更新：只改存在的列（不存在的列被忽略）
        strict_table.insert({'id': 1, 'name': 'Alice'})
        strict_table.update_by_rid(1, {'name': 'NewName', 'unknown': 'xxx'})
        row = strict_table.get_by_rid(1)
        self.test("灵活更新：只改存在列", 
                 row['name'] == 'NewName' and 'unknown' not in row)
        
        # 宽松模式更新：自动扩列
        loose_table.update_by_rid(1, {'city': 'Beijing'})
        self.test("loose 更新扩列", 'city' in loose_table.columns)
        
        # 切换模式
        strict_table.set_mode(SchemaMode.LOOSE)
        self.test("set_mode 切换", strict_table.mode == SchemaMode.LOOSE)
    
    def test_column_operations(self):
        """测试优化点3: 列操作"""
        print("\n▶ 列操作测试（优化点3）")
        
        table = Table('test', ['id', 'name'])
        table.insert({'id': 1, 'name': 'Alice'})
        table.insert({'id': 2, 'name': 'Bob'})
        
        # 添加列
        result = table.add_column('age', default_value=0)
        self.test("add_column 返回值", result)
        self.test("add_column 列存在", 'age' in table.columns)
        
        row = table.get_by_rid(1)
        self.test("add_column 默认值", row['age'] == 0)
        
        # 重复添加
        self.test("add_column 重复返回 False", not table.add_column('age'))
        
        # 删除列
        result = table.drop_column('age')
        self.test("drop_column 返回值", result)
        self.test("drop_column 列不存在", 'age' not in table.columns)
        
        row = table.get_by_rid(1)
        self.test("drop_column 数据删除", 'age' not in row)
        
        # 删除不存在的列
        self.test("drop_column 不存在返回 False", not table.drop_column('unknown'))
        
        # 重命名列
        table.add_column('old_col', 100)
        table.rename_column('old_col', 'new_col')
        self.test("rename_column", 'new_col' in table.columns and 'old_col' not in table.columns)
        self.test("rename_column 数据保留", table.get_by_rid(1)['new_col'] == 100)
    
    def test_encapsulation(self):
        """测试优化点4: 封装性"""
        print("\n▶ 封装性测试（优化点4）")
        
        table = Table('test', ['id', 'name'])
        table.insert({'id': 1, 'name': 'Alice'})
        
        # 属性只读
        try:
            table.name = 'new_name'
            self.test("name 只读", False)
        except AttributeError:
            self.test("name 只读", True)
        
        # columns 返回拷贝
        cols = table.columns
        cols.append('new_col')
        self.test("columns 返回拷贝", 'new_col' not in table.columns)
        
        # rid 只读
        row = table.get_by_rid(1)
        try:
            row['rid'] = 999
            # 注意：这里能修改是因为 to_dict 返回的是普通 dict
            # 但 Row 对象本身的 _rid 是不能修改的
        except:
            pass
        # 重新获取，rid 应该还是1
        row2 = table.get_by_rid(1)
        self.test("rid 不可修改", row2['rid'] == 1)

    def test_indexing(self):
        """测试索引功能，包括 B+树和删除维护"""
        print("\n▶ 索引测试")

        table = Table('orders', ['id', 'customer', 'amount'])
        table.insert({'id': 1, 'customer': 'Alice', 'amount': 30})
        table.insert({'id': 2, 'customer': 'Bob', 'amount': 10})
        table.insert({'id': 3, 'customer': 'Charlie', 'amount': 20})
        table.insert({'id': 4, 'customer': 'Diana', 'amount': 20})

        self.test("create_index(B+树)", table.create_index('amount', IndexType.BPLUS_TREE))
        self.test("重复创建索引返回 False", not table.create_index('amount', IndexType.BPLUS_TREE))

        eq_rows = table.search_by_index('amount', 20, columns=['customer'])
        self.test(
            "B+树等值查询",
            eq_rows == [
                {'rid': 3, 'customer': 'Charlie'},
                {'rid': 4, 'customer': 'Diana'},
            ],
        )

        range_rows = table.search_range_by_index('amount', 20, 30, columns=['customer'])
        self.test(
            "B+树范围查询",
            range_rows == [
                {'rid': 3, 'customer': 'Charlie'},
                {'rid': 4, 'customer': 'Diana'},
                {'rid': 1, 'customer': 'Alice'},
            ],
        )

        self.test("list_indices", table.list_indices() == ['amount'])
        self.test("无索引列返回 None", table.search_by_index('customer', 'Alice') is None)

        table.delete_by_rid(3)
        self.test(
            "delete_by_rid 维护索引",
            table.search_by_index('amount', 20, columns=['customer']) == [{'rid': 4, 'customer': 'Diana'}],
        )

        table.delete_by_index(1)  # 删除 rid=2
        self.test("delete_by_index 后行数正确", table.row_count == 2)
        self.test(
            "delete_by_index 维护索引",
            table.search_by_index('amount', 10, columns=['customer']) == [],
        )

        table.rename_column('amount', 'total')
        self.test("rename_column 保留索引", table.search_by_index('total', 30, columns=['customer']) == [{'rid': 1, 'customer': 'Alice'}])
    
    def test_database(self):
        """测试 Database 类"""
        print("\n▶ Database 测试")
        
        db = Database('test_db')
        
        # 创建表（指定模式）
        table = db.create_table('users', ['id', 'name'], mode=SchemaMode.LOOSE)
        self.test("create_table 带 mode", table.mode == SchemaMode.LOOSE)
        
        # 快捷操作
        rid = db.insert('users', {'id': 1, 'name': 'Alice'})
        
        # 指定列查询
        rows = db.select_all('users', columns=['name'])
        self.test("db.select_all 指定列", rows[0] == {'rid': 1, 'name': 'Alice'})
        
        # 按索引查询
        row = db.select_by_index('users', 0, columns=['id'])
        self.test("db.select_by_index", row == {'rid': 1, 'id': 1})
        
        # 按 rid 查询
        row = db.get_by_rid('users', 1, columns=['name'])
        self.test("db.get_by_rid 指定列", row == {'rid': 1, 'name': 'Alice'})

        self.test("db.create_index", db.create_index('users', 'id', IndexType.BPLUS_TREE))
        rows = db.search_by_index('users', 'id', 1, columns=['name'])
        self.test("db.search_by_index", rows == [{'rid': 1, 'name': 'Alice'}])
        
        # 列操作快捷方式
        db.add_column('users', 'age', 18)
        self.test("db.add_column", 'age' in db.get_table('users').columns)
        
        db.drop_column('users', 'age')
        self.test("db.drop_column", 'age' not in db.get_table('users').columns)
        
        # 获取 Table 对象直接操作
        t = db.get_table('users')
        self.test("get_table", t is not None and t.name == 'users')
    
    def test_persistence(self):
        """测试持久化"""
        print("\n▶ Persistence 测试")
        
        tmpdir = tempfile.mkdtemp()
        
        try:
            # 创建包含各种特性的数据库
            db1 = Database('test_db')
            db1.create_table('strict_t', ['id', 'name'], mode=SchemaMode.STRICT)
            db1.create_table('loose_t', ['id'], mode=SchemaMode.LOOSE)
            
            db1.insert('strict_t', {'id': 1, 'name': 'Alice'})
            db1.insert('loose_t', {'id': 1, 'extra': 'value'})  # 宽松模式扩列
            
            db1.add_column('strict_t', 'age', 20)
            db1.create_index('strict_t', 'id', IndexType.BPLUS_TREE)
            db1.create_index('strict_t', 'age')
            
            # 保存
            db_path = os.path.join(tmpdir, 'test.db')
            Persistence.save(db1, db_path)
            
            # 加载
            db2 = Persistence.load(db_path)
            
            self.test("持久化：数据库名", db2.name == 'test_db')
            self.test("持久化：表数量", len(db2.list_tables()) == 2)
            
            # 验证模式
            strict_t = db2.get_table('strict_t')
            loose_t = db2.get_table('loose_t')
            self.test("持久化：strict 模式", strict_t.mode == SchemaMode.STRICT)
            self.test("持久化：loose 模式", loose_t.mode == SchemaMode.LOOSE)
            
            # 验证数据
            self.test("持久化：数据保留", len(strict_t.get_all()) == 1)
            self.test("持久化：扩列保留", 'extra' in loose_t.columns)
            self.test("持久化：add_column 保留", 'age' in strict_t.columns)
            self.test(
                "持久化：索引保留",
                db2.search_by_index('strict_t', 'id', 1, columns=['name']) == [{'rid': 1, 'name': 'Alice'}],
            )
            self.test(
                "持久化：范围索引保留",
                db2.search_range_by_index('strict_t', 'age', 18, 30, columns=['name']) == [{'rid': 1, 'name': 'Alice'}],
            )
            
            # 验证 rid 连续
            new_rid = db2.insert('strict_t', {'id': 2, 'name': 'Bob', 'age': 25})
            self.test("持久化：rid 连续", new_rid == 2)
            
        finally:
            shutil.rmtree(tmpdir)
    
    def test_integration(self):
        """集成测试"""
        print("\n▶ 集成测试")
        
        # 场景：电商数据库
        db = Database('shop')
        
        # 创建宽松模式表（方便扩展）
        products = db.create_table('products', ['id', 'name', 'price'], mode=SchemaMode.LOOSE)
        
        # 插入基础数据
        db.insert('products', {'id': 1, 'name': 'iPhone', 'price': 5999})
        db.insert('products', {'id': 2, 'name': 'MacBook', 'price': 12999})
        
        # 后来需要添加分类，宽松模式自动扩列
        db.insert('products', {'id': 3, 'name': 'AirPods', 'price': 1299, 'category': 'audio'})
        
        self.test("集成：宽松模式扩列", 'category' in products.columns)
        
        # 查询：只需要名称和价格
        items = db.select_all('products', columns=['name', 'price'])
        self.test("集成：指定列查询", all('category' not in item for item in items))
        
        # 按索引获取第二个商品
        item = db.select_by_index('products', 1, columns=['name'])
        self.test("集成：按索引查询", item['name'] == 'MacBook')
        
        # 给所有商品添加库存列
        db.add_column('products', 'stock', 100)
        self.test("集成：批量添加列", all(r['stock'] == 100 for r in db.select_all('products')))
        
        # 更新时只改部分字段（宽松模式下未知列会扩列）
        products.update_by_rid(1, {'stock': 50})
        row = db.get_by_rid('products', 1)
        self.test("集成：灵活更新", row['stock'] == 50)
        
        print(f"\n  最终 products 表结构: {products.columns}")
        print(f"  数据:")
        for r in db.select_all('products'):
            print(f"    {r}")


def demo():
    """交互式演示"""
    print("\n" + "=" * 60)
    print("Storage 模块功能演示（优化版）")
    print("=" * 60)
    
    # 1. 创建数据库
    print("\n1. 创建数据库 'demo_db'")
    db = Database('demo_db')
    print(f"   创建成功: {db}")
    
    # 2. 创建宽松模式表
    print("\n2. 创建宽松模式表 'employees'")
    table = db.create_table('employees', ['id', 'name'], mode=SchemaMode.LOOSE)
    print(f"   模式: {table.mode.value}")
    print(f"   初始列: {table.columns}")
    
    # 3. 插入数据（后续自动扩列）
    print("\n3. 插入数据（观察自动扩列）")
    db.insert('employees', {'id': 1, 'name': '张三'})
    db.insert('employees', {'id': 2, 'name': '李四', 'department': '技术部'})  # 自动扩列
    db.insert('employees', {'id': 3, 'name': '王五', 'department': '市场部', 'salary': 15000})
    
    print(f"   插入后列: {table.columns}")
    print_table(db.select_all('employees'))
    
    # 4. 按索引查询
    print("\n4. 按索引查询（第0行）")
    row = db.select_by_index('employees', 0)
    print(f"   {row}")
    
    # 5. 指定列查询
    print("\n5. 指定列查询（只查 name 和 salary）")
    rows = db.select_all('employees', columns=['name', 'salary'])
    print_table(rows)
    
    # 6. 列操作
    print("\n6. 列操作：添加 'email' 列")
    db.add_column('employees', 'email', 'unknown@company.com')
    print_table(db.select_all('employees', columns=['name', 'email']))
    
    print("\n7. 列操作：删除 'department' 列")
    db.drop_column('employees', 'department')
    print(f"   删除后列: {table.columns}")
    
    # 8. 灵活更新
    print("\n8. 灵活更新：只更新 email，忽略不存在的列")
    db.update('employees', 1, {'email': 'zhangsan@company.com', 'nonexistent': 'xxx'})
    row = db.get_by_rid('employees', 1, columns=['name', 'email'])
    print(f"   更新后: {row}")
    
    # 9. 按索引删除
    print("\n9. 按索引删除（删除第1行）")
    db.delete_by_index('employees', 1)
    print_table(db.select_all('employees'))
    
    print("\n" + "=" * 60)
    print("演示完成！")
    print("=" * 60)


def print_table(rows):
    """美观地打印表格"""
    if not rows:
        print("   (空表)")
        return
    
    columns = list(rows[0].keys())
    widths = {col: max(len(str(col)), max(len(str(r.get(col, ''))) for r in rows)) + 2 for col in columns}
    
    sep = "+" + "+".join("-" * (widths[col] + 2) for col in columns) + "+"
    
    print(f"   {sep}")
    header = "|".join(str(col).center(widths[col] + 2) for col in columns)
    print(f"   |{header}|")
    print(f"   {sep}")
    
    for row in rows:
        line = "|".join(str(row.get(col, '')).ljust(widths[col] + 2) for col in columns)
        print(f"   |{line}|")
    
    print(f"   {sep}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Storage 模块测试')
    parser.add_argument('--demo', action='store_true', help='运行演示')
    
    args = parser.parse_args()
    
    if args.demo:
        demo()
    else:
        runner = TestRunner()
        success = runner.run_all()
        sys.exit(0 if success else 1)
