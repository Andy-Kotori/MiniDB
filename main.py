#!/usr/bin/env python3
"""
MiniDB - 完整的数据库系统
整合前端 REPL 和后端存储引擎
支持 MVCC + 事务
"""

import sys
import os
import json

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

from storage.database import Database
from storage.mvcc_database import MvccDatabase
from storage_adapter import StorageAdapter
from frontend.repl import MiniDBREPL


class Persistence:
    """简单的持久化包装器，适配前端接口"""

    def __init__(self, filename: str = "minidb.data"):
        self.filename = filename

    def save(self, data: dict) -> None:
        """保存数据"""
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            print(f"Data saved to {self.filename}")
        except Exception as e:
            print(f"Save failed: {e}")

    def load(self) -> dict:
        """加载数据"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Load failed: {e}")
        return None


def main():
    import argparse

    parser = argparse.ArgumentParser(description='MiniDB - 迷你数据库系统')
    parser.add_argument('--file', '-f', default='minidb.data', help='数据文件路径')
    parser.add_argument('--execute', '-e', help='执行单条 SQL 语句后退出')
    parser.add_argument('--mvcc', action='store_true', help='启用 MVCC 事务模式')
    parser.add_argument('--version', '-v', action='version', version='MiniDB 2.0.0')

    args = parser.parse_args()

    # 创建适配器（默认启用 MVCC）
    storage = StorageAdapter(use_mvcc=True) if args.mvcc else StorageAdapter()
    persistence = Persistence(args.file)

    # 尝试加载已有数据
    data = persistence.load()
    if data:
        try:
            storage.load_from_dict(data)
            print("Data loaded.")
        except Exception as e:
            print(f"Warning: could not load data: {e}")

    # 创建 REPL
    repl = MiniDBREPL(storage, persistence)

    if args.execute:
        # 单条 SQL 执行模式
        try:
            cmd = args.execute.strip()
            if cmd.startswith('.'):
                repl.handle_dot_command(cmd)
            else:
                repl.execute_sql(cmd)
            # 如果不在事务中，才保存数据
            if not storage.in_transaction():
                persistence.save(storage.to_dict())
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        # 交互模式
        try:
            repl.run()
        except KeyboardInterrupt:
            print("\nGoodbye!")
            sys.exit(0)
        except Exception as e:
            print(f"Fatal error: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()