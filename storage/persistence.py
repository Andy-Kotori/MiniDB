"""
persistence.py - 持久化实现

提供 Database 的保存和加载功能：
- 格式: binary（默认）、pickle、json
- binary: 自定义页式二进制格式，按固定大小页写入
- pickle/json: 兼容旧格式，便于调试与对比

说明：
- binary 仍是一次性保存/加载整个 Database
- 已具备页式文件布局，但暂不实现缓冲池、增量刷盘和恢复机制
"""

import json
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .database import Database
from .page_manager import PageManager, PageSpan, PageType


class Persistence:
    """
    持久化管理器
    
    使用方式:
        db = Database()
        # ... 操作数据库 ...
        
        # 保存
        Persistence.save(db, 'mydb.db')
        
        # 加载
        db = Persistence.load('mydb.db')
    
    格式说明:
        - binary: 自定义页式二进制格式，默认用于 .db 文件
        - pickle: Python 专用，性能好，支持所有 Python 类型
        - json: 通用格式，可读，但只支持基本类型（int/float/str/list/dict）
    """

    DEFAULT_FORMAT = 'binary'
    PAGE_SIZE = PageManager.DEFAULT_PAGE_SIZE
    VERSION = PageManager.VERSION
    
    @staticmethod
    def save(db: Database, filepath: Union[str, Path], fmt: Optional[str] = None) -> None:
        """
        保存数据库到文件
        
        Args:
            db: Database 对象
            filepath: 文件路径
            fmt: 格式，'binary'、'pickle' 或 'json'，默认从文件扩展名推断
        
        Raises:
            ValueError: 格式不支持
            IOError: 文件写入失败
        """
        filepath = Path(filepath)
        
        fmt = Persistence._infer_format(filepath, fmt)
        
        # 确保目录存在
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # 序列化
        data = db.to_dict()
        
        if fmt == 'binary':
            Persistence._save_binary(db, filepath)
        elif fmt == 'pickle':
            with open(filepath, 'wb') as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        elif fmt == 'json':
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"不支持的格式: {fmt}，请使用 'binary'、'pickle' 或 'json'")
    
    @staticmethod
    def load(filepath: Union[str, Path], fmt: Optional[str] = None) -> Database:
        """
        从文件加载数据库
        
        Args:
            filepath: 文件路径
            fmt: 格式，'binary'、'pickle' 或 'json'，默认从文件扩展名推断
        
        Returns:
            Database 对象
        
        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 格式不支持或文件损坏
        """
        filepath = Path(filepath)
        
        if not filepath.exists():
            raise FileNotFoundError(f"数据库文件不存在: {filepath}")
        
        fmt = Persistence._infer_format(filepath, fmt)
        
        # 反序列化
        if fmt == 'binary':
            return Persistence._load_binary(filepath)
        elif fmt == 'pickle':
            with open(filepath, 'rb') as f:
                data = pickle.load(f)
        elif fmt == 'json':
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            raise ValueError(f"不支持的格式: {fmt}")
        
        return Database.from_dict(data)

    @staticmethod
    def _infer_format(filepath: Path, fmt: Optional[str]) -> str:
        """根据扩展名推断格式"""
        if fmt is not None:
            return fmt

        if filepath.suffix == '.json':
            return 'json'
        if filepath.suffix in {'.pkl', '.pickle'}:
            return 'pickle'
        return Persistence.DEFAULT_FORMAT

    @staticmethod
    def _save_binary(db: Database, filepath: Path) -> None:
        """保存为页式二进制文件"""
        db_dict = db.to_dict()
        manager = PageManager(filepath, page_size=Persistence.PAGE_SIZE)

        table_blobs: List[Dict[str, Any]] = []
        for table_name, table_dict in db_dict['tables'].items():
            table_blobs.append({
                'name': table_name,
                'columns': table_dict['columns'],
                'next_rid': table_dict['next_rid'],
                'mode': table_dict['mode'],
                'row_blob': pickle.dumps(table_dict.get('rows', []), protocol=pickle.HIGHEST_PROTOCOL),
                'index_blob': pickle.dumps(table_dict.get('indices', {}), protocol=pickle.HIGHEST_PROTOCOL),
            })

        directory_bytes, directory_page_count = Persistence._build_directory_layout(manager, db_dict['name'], table_blobs)

        manager.reset()
        directory_span = manager.append_payload(directory_bytes, PageType.DIRECTORY)
        if directory_span.page_count != directory_page_count:
            raise ValueError("目录页数量计算不一致")

        for table_blob in table_blobs:
            manager.append_payload(table_blob['row_blob'], PageType.ROWS)
            manager.append_payload(table_blob['index_blob'], PageType.INDICES)

        manager.write(directory_page_count=directory_span.page_count)

    @staticmethod
    def _load_binary(filepath: Path) -> Database:
        """从页式二进制文件加载数据库"""
        manager = PageManager(filepath, page_size=Persistence.PAGE_SIZE)
        header = manager.load()

        directory_span = PageSpan(
            page_type=PageType.DIRECTORY,
            start_page_id=0,
            page_count=header.directory_page_count,
            payload_size=header.directory_page_count * manager.payload_capacity(),
        )
        directory_bytes = manager.read_span(directory_span, expected_page_type=PageType.DIRECTORY)
        directory = json.loads(directory_bytes.decode('utf-8'))

        db_dict: Dict[str, Any] = {
            'name': directory['name'],
            'tables': {}
        }

        consumed_pages = header.directory_page_count
        for table_meta in directory.get('tables', []):
            row_span = PageSpan(
                page_type=PageType.ROWS,
                start_page_id=table_meta['row_start_page_id'],
                page_count=table_meta['row_page_count'],
                payload_size=table_meta['row_payload_size'],
            )
            index_span = PageSpan(
                page_type=PageType.INDICES,
                start_page_id=table_meta['index_start_page_id'],
                page_count=table_meta['index_page_count'],
                payload_size=table_meta['index_payload_size'],
            )

            row_bytes = manager.read_span(row_span, expected_page_type=PageType.ROWS)
            index_bytes = manager.read_span(index_span, expected_page_type=PageType.INDICES)

            consumed_pages += row_span.page_count + index_span.page_count
            rows = pickle.loads(row_bytes) if row_bytes else []
            indices = pickle.loads(index_bytes) if index_bytes else {}

            db_dict['tables'][table_meta['name']] = {
                'name': table_meta['name'],
                'columns': table_meta['columns'],
                'next_rid': table_meta['next_rid'],
                'mode': table_meta['mode'],
                'rows': rows,
                'indices': indices,
            }

        if consumed_pages != header.total_page_count:
            raise ValueError("数据库文件损坏: 页数统计不一致")

        return Database.from_dict(db_dict)

    @staticmethod
    def _build_directory_layout(
        manager: PageManager,
        db_name: str,
        table_blobs: List[Dict[str, Any]],
    ) -> tuple[bytes, int]:
        """根据页范围构建稳定的目录页内容"""
        directory_page_count = 1

        while True:
            next_page_id = directory_page_count
            tables: List[Dict[str, Any]] = []

            for table_blob in table_blobs:
                row_page_count = manager.page_count_for_payload(table_blob['row_blob'])
                index_page_count = manager.page_count_for_payload(table_blob['index_blob'])

                row_start_page_id = next_page_id
                next_page_id += row_page_count
                index_start_page_id = next_page_id
                next_page_id += index_page_count

                tables.append({
                    'name': table_blob['name'],
                    'columns': table_blob['columns'],
                    'next_rid': table_blob['next_rid'],
                    'mode': table_blob['mode'],
                    'row_start_page_id': row_start_page_id,
                    'row_page_count': row_page_count,
                    'row_payload_size': len(table_blob['row_blob']),
                    'index_start_page_id': index_start_page_id,
                    'index_page_count': index_page_count,
                    'index_payload_size': len(table_blob['index_blob']),
                })

            directory = {
                'name': db_name,
                'version': Persistence.VERSION,
                'page_size': manager.page_size,
                'tables': tables,
            }
            directory_bytes = json.dumps(directory, ensure_ascii=False).encode('utf-8')
            new_directory_page_count = manager.page_count_for_payload(directory_bytes)

            if new_directory_page_count == directory_page_count:
                return directory_bytes, directory_page_count

            directory_page_count = new_directory_page_count
    
    @staticmethod
    def exists(filepath: Union[str, Path]) -> bool:
        """
        检查数据库文件是否存在
        """
        return Path(filepath).exists()
    
    @staticmethod
    def delete(filepath: Union[str, Path]) -> bool:
        """
        删除数据库文件
        
        Returns:
            是否成功删除
        """
        filepath = Path(filepath)
        if filepath.exists():
            filepath.unlink()
            return True
        return False
