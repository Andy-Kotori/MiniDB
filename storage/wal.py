"""
wal.py - 预写日志管理器 (Write-Ahead Log)

提供：
- 日志记录追加（带 fsync）
- 崩溃恢复（Redo + Undo）
- Checkpoint 机制
"""

import binascii
import json
import os
import struct
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class WALRecordType(Enum):
    """WAL 记录类型"""
    BEGIN = 1
    INSERT = 2
    UPDATE = 3
    DELETE = 4
    COMMIT = 5
    ROLLBACK = 6
    CHECKPOINT = 7


@dataclass
class WALRecord:
    """WAL 记录"""
    lsn: int
    xid: int
    type: WALRecordType
    table: str
    rid: int
    before_image: Optional[Dict[str, Any]] = None   # Undo 数据
    after_image: Optional[Dict[str, Any]] = None    # Redo 数据

    def to_bytes(self) -> bytes:
        """序列化为 JSON + 长度前缀 + checksum"""
        payload = {
            "lsn": self.lsn,
            "xid": self.xid,
            "type": self.type.value,
            "table": self.table,
            "rid": self.rid,
            "before": self.before_image,
            "after": self.after_image,
        }
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        # checksum = CRC32 of payload
        checksum = binascii.crc32(data) & 0xFFFFFFFF
        # format: [4 bytes len][4 bytes checksum][payload]
        header = struct.pack("<II", len(data), checksum)
        return header + data

    @classmethod
    def from_bytes(cls, raw: bytes) -> "WALRecord":
        """从字节反序列化"""
        if len(raw) < 8:
            raise ValueError("WAL record too short")
        payload_len, checksum = struct.unpack("<II", raw[:8])
        data = raw[8:8 + payload_len]
        # verify checksum
        actual = binascii.crc32(data) & 0xFFFFFFFF
        if actual != checksum:
            raise ValueError("WAL record checksum mismatch")
        payload = json.loads(data.decode("utf-8"))
        return cls(
            lsn=payload["lsn"],
            xid=payload["xid"],
            type=WALRecordType(payload["type"]),
            table=payload["table"],
            rid=payload["rid"],
            before_image=payload.get("before"),
            after_image=payload.get("after"),
        )


class WALManager:
    """
    WAL 管理器

    日志文件格式：
    [record1][record2][record3]...
    每个 record: [4 bytes payload_len][4 bytes checksum][payload]
    """

    def __init__(self, filepath: str = "mydb2.wal"):
        self.filepath = Path(filepath)
        self._lock = threading.RLock()
        self._lsn = 0
        self._flushed_lsn = 0
        self._file = None
        self._open()

    def _open(self) -> None:
        """打开或创建 WAL 文件"""
        mode = "ab" if self.filepath.exists() else "wb"
        self._file = open(self.filepath, mode)
        # 如果文件存在，扫描到最后一个 LSN
        if self.filepath.exists() and self.filepath.stat().st_size > 0:
            self._scan_last_lsn()

    def _scan_last_lsn(self) -> None:
        """扫描 WAL 文件找到最后一个 LSN"""
        try:
            with open(self.filepath, "rb") as f:
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    payload_len, _ = struct.unpack("<II", header)
                    payload = f.read(payload_len)
                    if len(payload) < payload_len:
                        break
                    record = WALRecord.from_bytes(header + payload)
                    self._lsn = max(self._lsn, record.lsn)
                    self._flushed_lsn = self._lsn
        except Exception:
            pass

    def append(
        self,
        xid: int,
        record_type: WALRecordType,
        table: str,
        rid: int,
        before_image: Optional[Dict] = None,
        after_image: Optional[Dict] = None,
    ) -> WALRecord:
        """
        追加 WAL 记录并 fsync
        """
        with self._lock:
            self._lsn += 1
            record = WALRecord(
                lsn=self._lsn,
                xid=xid,
                type=record_type,
                table=table,
                rid=rid,
                before_image=before_image,
                after_image=after_image,
            )
            raw = record.to_bytes()
            self._file.write(raw)
            self._file.flush()
            os.fsync(self._file.fileno())
            self._flushed_lsn = self._lsn
            return record

    def read_all(self) -> List[WALRecord]:
        """读取 WAL 文件中所有记录"""
        records = []
        if not self.filepath.exists():
            return records
        with open(self.filepath, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                payload_len, _ = struct.unpack("<II", header)
                payload = f.read(payload_len)
                if len(payload) < payload_len:
                    break
                try:
                    record = WALRecord.from_bytes(header + payload)
                    records.append(record)
                except ValueError:
                    # 损坏的记录，停止读取
                    break
        return records

    def checkpoint(self) -> int:
        """
        写入 CHECKPOINT 记录
        返回 checkpoint LSN
        """
        record = self.append(
            xid=0,
            record_type=WALRecordType.CHECKPOINT,
            table="",
            rid=0,
        )
        return record.lsn

    def truncate(self, before_lsn: int) -> None:
        """
        截断 before_lsn 之前的日志
        （在 checkpoint 后调用）
        """
        with self._lock:
            records = self.read_all()
            kept = [r for r in records if r.lsn >= before_lsn]
            self._file.close()
            with open(self.filepath, "wb") as f:
                for r in kept:
                    f.write(r.to_bytes())
            self._file = open(self.filepath, "ab")

    def close(self) -> None:
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None

    # ---------- 恢复 ----------

    def recover(
        self,
        tables: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[int]]:
        """
        崩溃恢复

        Args:
            tables: 当前内存中的表数据 {table_name: table_obj}

        Returns:
            (恢复后的 tables, 需要回滚的事务 xid 列表)
        """
        records = self.read_all()
        if not records:
            return tables, []

        # 找到最后一个 CHECKPOINT
        checkpoint_idx = -1
        for i in range(len(records) - 1, -1, -1):
            if records[i].type == WALRecordType.CHECKPOINT:
                checkpoint_idx = i
                break

        start_idx = checkpoint_idx if checkpoint_idx >= 0 else 0
        relevant = records[start_idx:]

        # 分析事务状态
        tx_state: Dict[int, str] = {}  # xid -> "committed" | "aborted" | "active"
        tx_records: Dict[int, List[WALRecord]] = {}  # xid -> [records]

        for r in relevant:
            if r.xid == 0:
                continue
            if r.xid not in tx_records:
                tx_records[r.xid] = []
            tx_records[r.xid].append(r)

            if r.type == WALRecordType.COMMIT:
                tx_state[r.xid] = "committed"
            elif r.type == WALRecordType.ROLLBACK:
                tx_state[r.xid] = "aborted"

        # 未看到 COMMIT/ROLLBACK 的事务视为 active（需要回滚）
        active_xids = []
        for xid, records_list in tx_records.items():
            if xid not in tx_state:
                tx_state[xid] = "active"
                active_xids.append(xid)

        # Phase 1: Redo — 重放所有已提交事务的操作
        for xid, state in tx_state.items():
            if state != "committed":
                continue
            for r in tx_records.get(xid, []):
                if r.type in (WALRecordType.INSERT, WALRecordType.UPDATE):
                    self._redo(tables, r)
                elif r.type == WALRecordType.DELETE:
                    self._redo_delete(tables, r)

        # Phase 2: Undo — 对 active 事务执行回滚
        for xid in active_xids:
            # 逆序执行 undo
            for r in reversed(tx_records.get(xid, [])):
                if r.type == WALRecordType.INSERT:
                    self._undo_insert(tables, r)
                elif r.type == WALRecordType.UPDATE:
                    self._undo_update(tables, r)
                elif r.type == WALRecordType.DELETE:
                    self._undo_delete(tables, r)

        return tables, active_xids

    def _redo(self, tables: Dict[str, Any], record: WALRecord) -> None:
        """Redo: 应用 after_image"""
        table = tables.get(record.table)
        if table is None:
            return
        if hasattr(table, "_version_store"):
            # MVCC table
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
            table._version_store.update(record.rid, record.after_image, tx)
        elif hasattr(table, "_rows"):
            # 普通 table
            for row in table._rows:
                if row.rid == record.rid:
                    row.update(record.after_image)
                    break

    def _redo_delete(self, tables: Dict[str, Any], record: WALRecord) -> None:
        """Redo delete"""
        table = tables.get(record.table)
        if table is None:
            return
        if hasattr(table, "_version_store"):
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
            table._version_store.delete(record.rid, tx)

    def _undo_insert(self, tables: Dict[str, Any], record: WALRecord) -> None:
        """Undo insert: 删除插入的行"""
        table = tables.get(record.table)
        if table is None:
            return
        if hasattr(table, "_version_store"):
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
            table._version_store.delete(record.rid, tx)

    def _undo_update(self, tables: Dict[str, Any], record: WALRecord) -> None:
        """Undo update: 恢复 before_image"""
        table = tables.get(record.table)
        if table is None or record.before_image is None:
            return
        if hasattr(table, "_version_store"):
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
            table._version_store.update(record.rid, record.before_image, tx)

    def _undo_delete(self, tables: Dict[str, Any], record: WALRecord) -> None:
        """Undo delete: 重新插入 before_image"""
        table = tables.get(record.table)
        if table is None or record.before_image is None:
            return
        if hasattr(table, "_version_store"):
            from .transaction import Transaction
            tx = Transaction(xid=0, start_ts=0)
            table._version_store.insert(record.before_image, tx)
