"""
page_manager.py - 磁盘页管理

提供固定大小页的编码、写入、读取和页范围管理。
当前实现包含一个轻量级页缓存，但不实现替换策略和脏页回收。
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional, Union


class PageType(IntEnum):
    """页类型"""

    DIRECTORY = 1
    ROWS = 2
    INDICES = 3


@dataclass(frozen=True)
class PageSpan:
    """一段连续页范围"""

    page_type: PageType
    start_page_id: int
    page_count: int
    payload_size: int


@dataclass(frozen=True)
class PageFileHeader:
    """页文件头信息"""

    magic: bytes
    version: int
    page_size: int
    directory_page_count: int
    total_page_count: int


class DiskPage:
    """单个磁盘页"""

    HEADER_STRUCT = struct.Struct("<BI")

    def __init__(
        self,
        page_id: int,
        page_type: PageType,
        payload: bytes,
        page_size: int,
    ):
        self.page_id = page_id
        self.page_type = page_type
        self.payload = payload
        self.page_size = page_size

    def to_bytes(self) -> bytes:
        """编码为固定大小页"""
        if len(self.payload) > self.payload_capacity(self.page_size):
            raise ValueError("页负载超过单页容量")

        header = self.HEADER_STRUCT.pack(int(self.page_type), len(self.payload))
        padding = b"\x00" * (self.page_size - len(header) - len(self.payload))
        return header + self.payload + padding

    @classmethod
    def from_bytes(cls, page_id: int, raw_page: bytes, page_size: int) -> "DiskPage":
        """从固定大小页反序列化"""
        if len(raw_page) != page_size:
            raise ValueError("页大小不正确")

        page_type, payload_size = cls.HEADER_STRUCT.unpack(raw_page[: cls.HEADER_STRUCT.size])
        payload_start = cls.HEADER_STRUCT.size
        payload_end = payload_start + payload_size
        payload = raw_page[payload_start:payload_end]
        return cls(page_id, PageType(page_type), payload, page_size)

    @classmethod
    def payload_capacity(cls, page_size: int) -> int:
        """单页可用负载大小"""
        return page_size - cls.HEADER_STRUCT.size


class PageManager:
    """
    磁盘页管理器

    能力：
    - 为负载分配连续页范围
    - 写入自定义页式二进制文件
    - 从文件中加载全部页到轻量缓存
    - 按页范围读取负载
    """

    MAGIC = b"MYDBPAGE"
    VERSION = 1
    DEFAULT_PAGE_SIZE = 4096
    FILE_HEADER = struct.Struct("<8sIIII")

    def __init__(self, filepath: Union[str, Path], page_size: int = DEFAULT_PAGE_SIZE):
        self.filepath = Path(filepath)
        self.page_size = page_size
        self._pages: List[DiskPage] = []
        self._page_cache: Dict[int, DiskPage] = {}
        self._header: Optional[PageFileHeader] = None

    @property
    def header(self) -> Optional[PageFileHeader]:
        """已加载的文件头"""
        return self._header

    @property
    def page_count(self) -> int:
        """当前内存中的页数"""
        return len(self._pages)

    def payload_capacity(self) -> int:
        """单页可用负载大小"""
        return DiskPage.payload_capacity(self.page_size)

    def page_count_for_payload(self, payload: bytes) -> int:
        """计算一段负载需要多少页"""
        if not payload:
            return 0

        capacity = self.payload_capacity()
        return (len(payload) + capacity - 1) // capacity

    def reset(self) -> None:
        """清空当前内存中的页布局"""
        self._pages.clear()
        self._page_cache.clear()
        self._header = None

    def append_payload(self, payload: bytes, page_type: PageType) -> PageSpan:
        """把一段负载追加为连续页并返回页范围"""
        if not payload:
            return PageSpan(page_type, len(self._pages), 0, 0)

        start_page_id = len(self._pages)
        capacity = self.payload_capacity()

        for offset in range(0, len(payload), capacity):
            chunk = payload[offset:offset + capacity]
            page_id = len(self._pages)
            page = DiskPage(page_id, page_type, chunk, self.page_size)
            self._pages.append(page)
            self._page_cache[page_id] = page

        return PageSpan(page_type, start_page_id, len(self._pages) - start_page_id, len(payload))

    def write(self, directory_page_count: int) -> None:
        """把当前页布局写入磁盘"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        header = PageFileHeader(
            magic=self.MAGIC,
            version=self.VERSION,
            page_size=self.page_size,
            directory_page_count=directory_page_count,
            total_page_count=len(self._pages),
        )
        self._header = header

        with open(self.filepath, "wb") as f:
            f.write(
                self.FILE_HEADER.pack(
                    header.magic,
                    header.version,
                    header.page_size,
                    header.directory_page_count,
                    header.total_page_count,
                )
            )
            for page in self._pages:
                f.write(page.to_bytes())

    def load(self) -> PageFileHeader:
        """从磁盘读取文件头和全部页"""
        self.reset()

        with open(self.filepath, "rb") as f:
            header_bytes = f.read(self.FILE_HEADER.size)
            if len(header_bytes) != self.FILE_HEADER.size:
                raise ValueError("数据库文件损坏: 文件头不完整")

            magic, version, page_size, directory_page_count, total_page_count = self.FILE_HEADER.unpack(header_bytes)
            if magic != self.MAGIC:
                raise ValueError("数据库文件损坏: 非法文件头")
            if version != self.VERSION:
                raise ValueError(f"不支持的 binary 版本: {version}")
            if page_size != self.page_size:
                raise ValueError(f"不支持的页大小: {page_size}")

            self._header = PageFileHeader(
                magic=magic,
                version=version,
                page_size=page_size,
                directory_page_count=directory_page_count,
                total_page_count=total_page_count,
            )

            for page_id in range(total_page_count):
                raw_page = f.read(self.page_size)
                if len(raw_page) != self.page_size:
                    raise ValueError("数据库文件损坏: 页内容不完整")

                page = DiskPage.from_bytes(page_id, raw_page, self.page_size)
                self._pages.append(page)
                self._page_cache[page_id] = page

        return self._header

    def get_page(self, page_id: int) -> DiskPage:
        """从页缓存中获取单页"""
        if page_id not in self._page_cache:
            raise KeyError(f"页 {page_id} 不存在")
        return self._page_cache[page_id]

    def read_span(self, span: PageSpan, expected_page_type: Optional[PageType] = None) -> bytes:
        """按页范围读取完整负载"""
        if span.page_count == 0:
            return b""

        payload_parts: List[bytes] = []
        for page_id in range(span.start_page_id, span.start_page_id + span.page_count):
            page = self.get_page(page_id)
            if expected_page_type is not None and page.page_type != expected_page_type:
                raise ValueError("数据库文件损坏: 页类型不匹配")
            payload_parts.append(page.payload)

        return b"".join(payload_parts)[: span.payload_size]
