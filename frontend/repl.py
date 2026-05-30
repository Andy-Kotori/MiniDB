import sqlparse
from sqlparse.sql import Parenthesis, Identifier, Where, IdentifierList, Comparison
from sqlparse.tokens import Keyword, Wildcard

from frontend.where_parser import WhereParser, Condition
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory


class MiniDBREPL:
    def __init__(self, storage, persistence):
        self.storage = storage
        self.persistence = persistence
        self.session = PromptSession(history=FileHistory('.minidb_history'))
        self.running = True

    def run(self):
        # 加载持久化数据
        try:
            data = self.persistence.load()
            if data:
                self.storage.load_from_dict(data)
                print("Data loaded.")
        except Exception as e:
            print(f"Warning: could not load data: {e}")

        print("MiniDB started. Type '.exit' to quit, '.help' for help.")
        while self.running:
            try:
                cmd = self.session.prompt('minidb> ').strip()
                if not cmd:
                    continue
                if cmd.startswith('.'):
                    self.handle_dot_command(cmd)
                else:
                    self.execute_sql(cmd)
            except EOFError:
                print()
                self.do_exit()
                break
            except KeyboardInterrupt:
                print("^C")
                continue
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"Error: {e}")

    def handle_dot_command(self, cmd):
        if cmd == '.exit':
            self.do_exit()
        elif cmd == '.help':
            self.print_help()
        elif cmd == '.tables':
            self.show_tables()
        elif cmd.startswith('.schema'):
            self.show_schema(cmd)
        elif cmd == '.begin':
            self.do_begin()
        elif cmd == '.commit':
            self.do_commit()
        elif cmd == '.rollback':
            self.do_rollback()
        elif cmd.startswith('.savepoint'):
            self.do_savepoint(cmd)
        elif cmd.startswith('.rollback_to'):
            self.do_rollback_to(cmd)
        elif cmd == '.tx':
            self.show_transaction_status()
        elif cmd == '.locks':
            self.show_locks()
        else:
            print(f"Unknown dot command: {cmd}")

    def do_exit(self):
        try:
            # 如果有活跃事务，先回滚
            if self.storage.in_transaction():
                print("Warning: active transaction found, rolling back...")
                self.storage.rollback()
            data = self.storage.to_dict()
            self.persistence.save(data)
            print("Data saved.")
        except Exception as e:
            print(f"Error saving data: {e}")
        self.running = False

    def print_help(self):
        print("\n=== MiniDB Help ===")
        print("\nSQL Statements:")
        print("  CREATE TABLE <name> (<col1> <type>, <col2> <type>, ...)")
        print("  INSERT INTO <name> VALUES (<val1>, <val2>, ...)")
        print("  SELECT <cols> FROM <name> [WHERE <cond> [AND <cond>]]")
        print("  UPDATE <name> SET <col>=<val> [,<col>=<val>] [WHERE <cond>]")
        print("  DELETE FROM <name> [WHERE <cond>]")
        print("  DROP TABLE <name>")
        print("\nTransaction Commands:")
        print("  .begin                    - Start a transaction")
        print("  .commit                   - Commit current transaction")
        print("  .rollback                 - Rollback current transaction")
        print("  .savepoint <name>         - Set a savepoint")
        print("  .rollback_to <name>       - Rollback to savepoint")
        print("\nDot Commands:")
        print("  .exit        - Exit MiniDB")
        print("  .help        - Show this help")
        print("  .tables      - List all tables")
        print("  .schema 表名 - Show table schema")
        print("  .tx          - Show transaction status")
        print("  .locks       - Show lock status")
        print("==================\n")

    def show_tables(self):
        tables = self.storage.get_table_names()
        if not tables:
            print("No tables found.")
        else:
            print("Tables:")
            for table in tables:
                print(f"  {table}")

    def show_schema(self, cmd):
        parts = cmd.split()
        if len(parts) < 2:
            print("Usage: .schema <table_name>")
            return
        table_name = parts[1]
        schema = self.storage.get_schema(table_name)
        if schema:
            print(f"Table: {table_name}")
            print(f"Columns: {', '.join(schema)}")
        else:
            print(f"Table '{table_name}' not found.")

    # ---------- 事务命令 ----------

    def do_begin(self):
        try:
            if not self.storage.use_mvcc:
                self.storage.enable_mvcc()
            self.storage.begin()
        except Exception as e:
            print(f"Error: {e}")

    def do_commit(self):
        try:
            self.storage.commit()
        except Exception as e:
            print(f"Error: {e}")

    def do_rollback(self):
        try:
            self.storage.rollback()
        except Exception as e:
            print(f"Error: {e}")

    def do_savepoint(self, cmd):
        parts = cmd.split()
        if len(parts) < 2:
            print("Usage: .savepoint <name>")
            return
        try:
            self.storage.savepoint(parts[1])
        except Exception as e:
            print(f"Error: {e}")

    def do_rollback_to(self, cmd):
        parts = cmd.split()
        if len(parts) < 2:
            print("Usage: .rollback_to <name>")
            return
        try:
            self.storage.rollback_to_savepoint(parts[1])
        except Exception as e:
            print(f"Error: {e}")

    def show_transaction_status(self):
        if self.storage.in_transaction():
            print("Transaction: ACTIVE")
        else:
            print("Transaction: NONE")
        print(f"MVCC mode: {'ON' if self.storage.use_mvcc else 'OFF'}")

    def show_locks(self):
        if not self.storage.use_mvcc:
            print("MVCC mode is OFF. No locks.")
            return
        info = self.storage.db.get_lock_info()
        if not info:
            print("No active locks.")
            return
        print("Active locks:")
        for key, detail in info.items():
            print(f"  {key}: holders={detail['holders']}, waiters={detail['waiters']}")

    def execute_sql(self, sql):
        statements = sqlparse.split(sql)
        for stmt_text in statements:
            if not stmt_text.strip():
                continue
            parsed = sqlparse.parse(stmt_text)
            if not parsed:
                print("Empty SQL statement.")
                continue
            stmt = parsed[0]
            stmt_type = stmt.get_type()
            try:
                if stmt_type == 'CREATE':
                    self._handle_create(stmt)
                elif stmt_type == 'INSERT':
                    self._handle_insert(stmt)
                elif stmt_type == 'SELECT':
                    self._handle_select(stmt)
                elif stmt_type == 'UPDATE':
                    self._handle_update(stmt)
                elif stmt_type == 'DELETE':
                    self._handle_delete(stmt)
                elif stmt_type == 'DROP':
                    self._handle_drop(stmt)
                else:
                    print(f"Unsupported SQL type: {stmt_type or 'UNKNOWN'}")
            except Exception as e:
                print(f"Error executing SQL: {e}")

    def _handle_create(self, stmt):
        """处理 CREATE TABLE 语句"""
        table_name = None
        columns = []
        
        # 遍历顶层 token
        for i, token in enumerate(stmt.tokens):
            if token.is_whitespace:
                continue
                
            # 查找 TABLE 关键字
            if token.ttype is Keyword and token.value.upper() == 'TABLE':
                # 表名是 TABLE 后的下一个非空白 token
                for j in range(i + 1, len(stmt.tokens)):
                    nxt = stmt.tokens[j]
                    if not nxt.is_whitespace:
                        table_name = nxt.value.strip('"\'`')
                        break
                        
            # 查找括号内的列定义
            elif isinstance(token, Parenthesis):
                columns = self._extract_columns_from_parenthesis(token)
                break
        
        if not table_name:
            raise ValueError("Could not find table name in CREATE TABLE")
        if not columns:
            raise ValueError("No columns found in CREATE TABLE")
        
        self.storage.create_table(table_name, columns)
        print(f"Table '{table_name}' created with columns: {columns}")

    def _extract_columns_from_parenthesis(self, parenthesis):
        """从 Parenthesis token 中提取所有列名"""
        from sqlparse.sql import Identifier, IdentifierList
        columns = []
        for sub in parenthesis.tokens:
            if isinstance(sub, Identifier):
                columns.append(sub.get_name())
            elif isinstance(sub, IdentifierList):
                for item in sub.tokens:
                    if isinstance(item, Identifier):
                        columns.append(item.get_name())
        return columns

    def _extract_column_name(self, column_tokens):
        """从列定义的 token 列表中提取列名"""
        if not column_tokens:
            return None
        
        # 第一个 token 通常是列名
        first_token = column_tokens[0]
        if hasattr(first_token, 'value'):
            return first_token.value.strip('"\'`')
        return None

    def _handle_insert(self, stmt):
        """处理 INSERT INTO 语句 - 混合解析版（先 token 解析，失败后回退到字符串解析）"""
        table_name = None
        values = None
        
        # ========== 方法1：尝试 token 解析 ==========
        try:
            # 遍历所有 token
            for i, token in enumerate(stmt.tokens):
                if token.is_whitespace:
                    continue
                
                # 获取表名（INTO 后面）
                if token.ttype is Keyword and token.value.upper() == 'INTO':
                    # 找下一个非空白 token 作为表名
                    for j in range(i + 1, len(stmt.tokens)):
                        if not stmt.tokens[j].is_whitespace:
                            table_name = stmt.tokens[j].value.strip('"\'`')
                            break
                
                # 获取 VALUES 后的括号内容
                elif token.ttype is Keyword and token.value.upper() == 'VALUES':
                    # 找下一个非空白 token（应该是括号）
                    for j in range(i + 1, len(stmt.tokens)):
                        nxt = stmt.tokens[j]
                        if not nxt.is_whitespace:
                            if isinstance(nxt, Parenthesis):
                                # 是 Parenthesis 对象，直接解析
                                values = self._parse_parenthesis_values(nxt)
                            break
                    break
            
            # 如果 token 解析成功，直接使用结果
            if values is not None and table_name is not None:
                self.storage.insert(table_name, values)
                print(f"Inserted into '{table_name}': {values}")
                return
                
        except Exception as e:
            # token 解析失败，继续使用方法2
            pass
        
        # ========== 方法2：字符串解析（回退方案）==========
        stmt_str = str(stmt).strip()
        stmt_upper = stmt_str.upper()
        
        # 查找 INSERT INTO
        insert_idx = stmt_upper.find('INSERT INTO')
        if insert_idx == -1:
            raise ValueError("Not an INSERT statement")
        
        # 查找 VALUES
        values_idx = stmt_upper.find('VALUES')
        if values_idx == -1:
            raise ValueError("Could not find VALUES clause")
        
        # 提取表名
        table_part = stmt_str[insert_idx + 11:values_idx].strip()
        if table_part:
            table_name = table_part.split()[0].strip('"\'`')
        
        # 提取 VALUES 后的括号内容
        after_values = stmt_str[values_idx + 6:].strip()
        
        # 找到匹配的括号
        if after_values.startswith('('):
            depth = 0
            end_pos = 0
            for i, ch in enumerate(after_values):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        end_pos = i + 1
                        break
            
            if end_pos > 0:
                values_str = after_values[:end_pos]
                # 解析值
                values = self._parse_values_from_string(values_str)
        
        if not table_name:
            raise ValueError("Could not find table name in INSERT")
        if values is None:
            raise ValueError("Could not parse VALUES clause in INSERT")
        
        self.storage.insert(table_name, values)
        print(f"Inserted into '{table_name}': {values}")

    def _parse_parenthesis_values(self, paren_token):
        """从 Parenthesis token 中提取值列表"""
        values = []
        current_value = []
        
        for token in paren_token.tokens:
            if token.is_whitespace:
                continue
            
            if token.value == ',':
                # 一个值结束
                if current_value:
                    val_str = self._tokens_to_string(current_value)
                    values.append(self._parse_literal(val_str))
                    current_value = []
            else:
                current_value.append(token)
        
        # 最后一个值
        if current_value:
            val_str = self._tokens_to_string(current_value)
            values.append(self._parse_literal(val_str))
        
        return values

    def _parse_values_from_string(self, values_str):
        """从括号字符串中提取值（当 Parenthesis 对象不可用时）"""
        # 去掉最外层括号
        if values_str.startswith('(') and values_str.endswith(')'):
            values_str = values_str[1:-1]
        
        values = []
        current = []
        in_quote = False
        quote_char = None
        i = 0
        
        while i < len(values_str):
            ch = values_str[i]
            
            # 处理转义字符
            if ch == '\\' and i + 1 < len(values_str):
                current.append(ch)
                current.append(values_str[i + 1])
                i += 2
                continue
            
            # 开始引号
            if not in_quote and ch in ('"', "'"):
                in_quote = True
                quote_char = ch
                current.append(ch)
            # 结束引号
            elif in_quote and ch == quote_char:
                in_quote = False
                quote_char = None
                current.append(ch)
            # 分隔符（不在引号内）
            elif not in_quote and ch == ',':
                val_str = ''.join(current).strip()
                values.append(self._parse_literal(val_str))
                current = []
            # 普通字符
            else:
                current.append(ch)
            
            i += 1
        
        # 最后一个值
        if current:
            val_str = ''.join(current).strip()
            values.append(self._parse_literal(val_str))
        
        return values

    def _tokens_to_string(self, tokens):
        """将 token 列表转换为字符串"""
        result = []
        for token in tokens:
            if hasattr(token, 'value'):
                result.append(token.value)
            else:
                result.append(str(token))
        return ''.join(result)

    def _parse_literal(self, val_str):
        """将字符串字面量转换为 Python 对象"""
        val_str = val_str.strip()
        
        if not val_str:
            return None
        
        # NULL 值
        if val_str.upper() == 'NULL':
            return None
        
        # 字符串（单引号）
        if val_str.startswith("'") and val_str.endswith("'"):
            # 去掉引号，处理内部转义
            inner = val_str[1:-1]
            inner = inner.replace("\\'", "'").replace('\\"', '"')
            return inner
        
        # 字符串（双引号）
        if val_str.startswith('"') and val_str.endswith('"'):
            inner = val_str[1:-1]
            inner = inner.replace('\\"', '"').replace("\\'", "'")
            return inner
        
        # 布尔值
        if val_str.upper() == 'TRUE':
            return True
        if val_str.upper() == 'FALSE':
            return False
        
        # 数字
        try:
            return int(val_str)
        except ValueError:
            try:
                return float(val_str)
            except ValueError:
                # 其他情况作为字符串
                return val_str

    def _handle_select(self, stmt):
        """处理 SELECT 语句（支持 WHERE）"""
        table_name = None
        select_columns = None
        where_token = None

        for token in stmt.tokens:
            if token.is_whitespace:
                continue

            # SELECT 后面的列
            if token.ttype is Wildcard:
                select_columns = None  # * 表示所有列
            elif isinstance(token, IdentifierList):
                select_columns = [ident.get_name() for ident in token.get_identifiers()]
            elif isinstance(token, Identifier):
                # 可能是列名或表名
                pass

            # FROM 后面的表名
            if token.ttype is Keyword and token.value.upper() == 'FROM':
                # 找下一个非空白 token 作为表名
                idx = stmt.tokens.index(token)
                for j in range(idx + 1, len(stmt.tokens)):
                    nxt = stmt.tokens[j]
                    if not nxt.is_whitespace:
                        if isinstance(nxt, Identifier):
                            table_name = nxt.get_name()
                        else:
                            table_name = nxt.value.strip('"\'`')
                        break

            # WHERE 子句
            if isinstance(token, Where):
                where_token = token

        # 再尝试从 flatten token 找表名（备用）
        if not table_name:
            from_seen = False
            for token in stmt.flatten():
                if token.is_whitespace:
                    continue
                if from_seen and token.ttype is None:
                    table_name = token.value.strip('"\'`')
                    break
                if token.ttype is Keyword and token.value.upper() == 'FROM':
                    from_seen = True

        if not table_name:
            raise ValueError("Could not find table name in SELECT")

        # 解析 WHERE 条件
        if where_token:
            parser = WhereParser()
            conditions = parser.parse(where_token)
            if not conditions:
                raise ValueError("Could not parse WHERE clause")

            # 检查是否有索引可以加速
            table = self.storage.db.get_table(table_name)
            indexed_cols = table.list_indices() if table else []
            index_cond = parser.find_index_condition(conditions, indexed_cols)

            if index_cond and self.storage.use_mvcc:
                # 使用索引查询
                tx = self.storage.db.begin() if not self.storage.in_transaction() else self.storage._current_tx
                try:
                    rows = table.search_by_index(index_cond.column, index_cond.value, tx)
                    if not self.storage.in_transaction():
                        self.storage.db.commit(tx)
                except Exception:
                    if not self.storage.in_transaction():
                        self.storage.db.rollback(tx)
                    raise
                # 对其他条件过滤
                if rows:
                    filtered = [r for r in rows if parser.evaluate(r, conditions)]
                else:
                    filtered = []
                result = self._format_result(table_name, filtered, select_columns)
            else:
                # 全表扫描 + 过滤
                result = self.storage.select_where(table_name, select_columns,
                                                   lambda r: parser.evaluate(r, conditions))
        else:
            # 无 WHERE
            result = self.storage.select_all(table_name)
            # 如果有指定列，需要过滤
            if select_columns:
                result = self._filter_columns(result, select_columns)

        if result and result.get('rows'):
            self._print_table(result)
        else:
            print("(empty)")

    def _filter_columns(self, result, columns):
        """从查询结果中过滤指定列"""
        if not result or not result.get('rows'):
            return result
        # 重新格式化
        new_rows = []
        for row in result['rows']:
            new_row = []
            for col in columns:
                if col in result['columns']:
                    idx = result['columns'].index(col)
                    new_row.append(row[idx])
            new_rows.append(new_row)
        return {'columns': columns, 'rows': new_rows}

    def _format_result(self, table_name, rows, select_columns):
        """将行列表格式化为前端需要的格式"""
        table = self.storage.db.get_table(table_name)
        columns = table.columns if table else []

        if select_columns:
            columns = [c for c in select_columns if c in columns]

        data_rows = []
        for row in rows:
            if select_columns:
                data_rows.append([row.get(c) for c in select_columns])
            else:
                data_rows.append([row.get(c) for c in columns])

        return {'columns': columns, 'rows': data_rows}

    def _handle_update(self, stmt):
        """处理 UPDATE 语句"""
        table_name = None
        set_pairs = {}  # column -> value
        where_token = None

        for i, token in enumerate(stmt.tokens):
            if token.is_whitespace:
                continue

            # UPDATE 后面的表名（token 0 是 UPDATE，token 2 应该是表名）
            if i == 0 and token.ttype is Keyword and token.value.upper() == 'UPDATE':
                pass  # 跳过 UPDATE 关键字
            elif table_name is None and isinstance(token, Identifier):
                table_name = token.get_name()

            # SET 后面的赋值
            if token.ttype is Keyword and token.value.upper() == 'SET':
                for j in range(i + 1, len(stmt.tokens)):
                    nxt = stmt.tokens[j]
                    if isinstance(nxt, IdentifierList):
                        for comp in nxt.get_identifiers():
                            if isinstance(comp, Comparison):
                                parts = [t for t in comp.tokens if not t.is_whitespace]
                                if len(parts) == 3:
                                    col = parts[0].value.strip('"\'`')
                                    val = self._parse_literal(parts[2].value)
                                    set_pairs[col] = val
                    elif isinstance(nxt, Comparison):
                        parts = [t for t in nxt.tokens if not t.is_whitespace]
                        if len(parts) == 3:
                            col = parts[0].value.strip('"\'`')
                            val = self._parse_literal(parts[2].value)
                            set_pairs[col] = val

            # WHERE 子句
            if isinstance(token, Where):
                where_token = token

        if not table_name:
            raise ValueError("Could not find table name in UPDATE")
        if not set_pairs:
            raise ValueError("No SET clause found in UPDATE")

        # 解析 WHERE 条件
        if where_token:
            parser = WhereParser()
            conditions = parser.parse(where_token)
            if not conditions:
                raise ValueError("Could not parse WHERE clause")

            count = self.storage.update_where(
                table_name,
                lambda r: parser.evaluate(r, conditions),
                set_pairs
            )
            print(f"Updated {count} row(s).")
        else:
            # 无 WHERE，更新所有行
            count = self.storage.update_where(
                table_name,
                lambda r: True,
                set_pairs
            )
            print(f"Updated {count} row(s).")

    def _handle_delete(self, stmt):
        """处理 DELETE 语句"""
        table_name = None
        where_token = None

        for i, token in enumerate(stmt.tokens):
            if token.is_whitespace:
                continue

            # DELETE FROM 后面的表名
            if token.ttype is Keyword and token.value.upper() == 'FROM':
                for j in range(i + 1, len(stmt.tokens)):
                    nxt = stmt.tokens[j]
                    if not nxt.is_whitespace:
                        if isinstance(nxt, Identifier):
                            table_name = nxt.get_name()
                        else:
                            table_name = nxt.value.strip('"\'`')
                        break

            # WHERE 子句
            if isinstance(token, Where):
                where_token = token

        # 备用：尝试 flatten 找表名
        if not table_name:
            from_seen = False
            for token in stmt.flatten():
                if token.is_whitespace:
                    continue
                if from_seen and token.ttype is None:
                    table_name = token.value.strip('"\'`')
                    break
                if token.ttype is Keyword and token.value.upper() == 'FROM':
                    from_seen = True

        if not table_name:
            raise ValueError("Could not find table name in DELETE")

        # 解析 WHERE 条件
        if where_token:
            parser = WhereParser()
            conditions = parser.parse(where_token)
            if not conditions:
                raise ValueError("Could not parse WHERE clause")

            count = self.storage.delete_where(
                table_name,
                lambda r: parser.evaluate(r, conditions)
            )
            print(f"Deleted {count} row(s).")
        else:
            # 无 WHERE，删除所有行
            count = self.storage.delete_where(
                table_name,
                lambda r: True
            )
            print(f"Deleted {count} row(s).")

    def _handle_drop(self, stmt):
        """处理 DROP TABLE 语句"""
        table_name = None

        for i, token in enumerate(stmt.tokens):
            if token.is_whitespace:
                continue

            if token.ttype is Keyword and token.value.upper() == 'TABLE':
                for j in range(i + 1, len(stmt.tokens)):
                    nxt = stmt.tokens[j]
                    if not nxt.is_whitespace:
                        if isinstance(nxt, Identifier):
                            table_name = nxt.get_name()
                        else:
                            table_name = nxt.value.strip('"\'`')
                        break

        if not table_name:
            raise ValueError("Could not find table name in DROP TABLE")

        if self.storage.drop_table(table_name):
            print(f"Table '{table_name}' dropped.")
        else:
            print(f"Table '{table_name}' not found.")

    def _print_table(self, data):
        """格式化打印表格数据"""
        # 支持两种数据格式
        if isinstance(data, dict):
            columns = data.get('columns', [])
            rows = data.get('rows', [])
        elif isinstance(data, list):
            if not data:
                print("(empty)")
                return
            columns = [f"col{i}" for i in range(len(data[0]))]
            rows = data
        else:
            print("(empty)")
            return
    
        if not rows:
            print("(empty)")
            return
    
        if not columns and rows:
            columns = [f"col{i}" for i in range(len(rows[0]))]
    
        # 计算每列的最大宽度
        col_widths = [len(str(columns[i])) for i in range(len(columns))]
        for row in rows:
            for i, val in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(val)))
    
        # 打印表头分隔线
        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    
        # 打印表头
        header = "|"
        for i, col in enumerate(columns):
            header += f" {str(col):<{col_widths[i]}} |"
        print(sep)
        print(header)
        print(sep)
    
        # 打印数据行
        for row in rows:
            line = "|"
            for i, val in enumerate(row):
                if i < len(col_widths):
                    line += f" {str(val):<{col_widths[i]}} |"
                else:
                    line += f" {str(val)} |"
            print(line)
    
        print(sep)