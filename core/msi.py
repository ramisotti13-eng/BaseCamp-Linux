"""Reading a Windows Installer package (.msi) without Windows.

An .msi is an OLE compound file (MS-CFB): a small file system of storages
and streams in fixed-size sectors, chained through a file allocation table
(the FAT); streams under 4 KiB live in a "mini stream" with a FAT of their
own. Windows Installer keeps its database in it, a string pool and one
stream per table, and usually the installer's files as an embedded cabinet
(core/cab.py reads that).

    with MsiPackage.open(path) as msi:
        msi.files()             # {File key: MsiFile(key, path, size, sequence)}
        msi.cabinets()          # names of the embedded cabinet streams
        stream = msi.open_stream("Base_Camp_.cab")   # seekable, read on demand

Streams are read from the file as they are asked for, sector run by sector
run, so opening a 250 MB installer reads a few hundred KiB (the header, the
FAT, the directory and the database tables). Only what BaseCamp needs to
take a few pictures out of Mountain's installer: no writing, no transforms,
no summary information.

The database, briefly (the layout Wine's msi.dll reads too):

  - stream names are packed: two characters of [0-9A-Za-z._] per UTF-16
    code unit in U+3800..U+47FF, one in U+4800..U+483F; a table's stream
    starts with U+4840;
  - _StringPool holds (length, refcount) pairs of 16-bit words, entry 0
    being the code page; _StringData the strings end to end. String ids
    are 2 bytes, or 3 when the code page word has bit 15 set;
  - _Columns says what columns a table has; a table stream holds its rows
    column by column, integers stored with their top bit flipped, 0 for
    NULL; a string column holds string ids.
"""
import bisect
import io
import struct
import sys
from array import array

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

MAXREGSECT = 0xFFFFFFFA
DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
NOSTREAM = 0xFFFFFFFF

TYPE_STORAGE = 1
TYPE_STREAM = 2
TYPE_ROOT = 5

_DIR_ENTRY = struct.Struct("<64sHBBIII16sIQQIQ")      # 128 bytes


class MsiError(Exception):
    """Not a compound file / an installer, or a damaged one."""


def _u32_array(data):
    a = array("I")
    if a.itemsize != 4:                 # pragma: no cover - every platform we run on
        a = array("L")
    a.frombytes(data)
    if sys.byteorder == "big":          # pragma: no cover
        a.byteswap()
    return a


# ── The compound file ───────────────────────────────────────────────────────

class DirEntry:
    __slots__ = ("sid", "name", "type", "left", "right", "child", "start", "size")

    def __init__(self, sid, name, type_, left, right, child, start, size):
        self.sid = sid
        self.name = name
        self.type = type_
        self.left = left
        self.right = right
        self.child = child
        self.start = start
        self.size = size

    def __repr__(self):
        return "DirEntry(%d, %r, type %d, %d bytes)" % (self.sid, self.name, self.type, self.size)


class _Runs:
    """A sector chain as runs of consecutive sectors: (first logical
    sector, first physical sector, count). A big stream of an installer is
    mostly one run, so this stays small where a list of sectors would hold
    hundreds of thousands of numbers."""

    def __init__(self):
        self.logical = []
        self.physical = []
        self.count = []
        self.sectors = 0

    def add(self, sect):
        if self.count and self.physical[-1] + self.count[-1] == sect:
            self.count[-1] += 1
        else:
            self.logical.append(self.sectors)
            self.physical.append(sect)
            self.count.append(1)
        self.sectors += 1

    def locate(self, index):
        """(physical sector, sectors left in its run) of logical sector `index`."""
        i = bisect.bisect_right(self.logical, index) - 1
        off = index - self.logical[i]
        return self.physical[i] + off, self.count[i] - off


class CompoundFile:
    """An OLE compound file read from the seekable binary file object `f`."""

    def __init__(self, f):
        self._f = f
        f.seek(0, 2)
        self.file_size = f.tell()
        f.seek(0)
        hdr = f.read(512)
        if len(hdr) < 512 or hdr[:8] != SIGNATURE:
            raise MsiError("not an OLE compound file")
        major, byte_order, shift, mini_shift = struct.unpack_from("<HHHH", hdr, 0x1A)
        if byte_order != 0xFFFE or (major, shift) not in ((3, 9), (4, 12)):
            raise MsiError("unsupported compound file (version %d, sector shift %d)"
                           % (major, shift))
        if mini_shift != 6:
            raise MsiError("unsupported mini sector size")
        self.sector_shift = shift
        self.sector_size = 1 << shift
        self.mini_sector_size = 1 << mini_shift
        (n_fat, first_dir, _sig, self.mini_cutoff, first_minifat, n_minifat,
         first_difat, n_difat) = struct.unpack_from("<IIIIIIII", hdr, 0x2C)
        if self.mini_cutoff != 4096:
            raise MsiError("unsupported mini stream cutoff")
        self._max_sector = (self.file_size >> shift)    # sectors that fit the file

        # The FAT: its sectors are listed in the header (109) and in the
        # DIFAT sectors chained from it.
        difat = list(struct.unpack_from("<109I", hdr, 0x4C))
        per = self.sector_size // 4 - 1
        sect, seen = first_difat, set()
        for _ in range(n_difat):
            if sect > MAXREGSECT:
                break
            if sect in seen:
                raise MsiError("the DIFAT loops")
            seen.add(sect)
            vals = _u32_array(self._sector(sect))
            difat.extend(vals[:per])
            sect = vals[per]
        if n_fat > len(difat) or n_fat > self._max_sector:
            raise MsiError("the header names more FAT sectors than there are")
        fat = bytearray()
        for s in difat[:n_fat]:
            if s > MAXREGSECT:
                raise MsiError("a FAT sector is missing")
            fat += self._sector(s)
        self._fat = _u32_array(bytes(fat))

        # The directory.
        raw = self._read_chain(first_dir, self._fat, self.sector_size)
        self.entries = []
        for sid in range(len(raw) // 128):
            (name, name_len, type_, _color, left, right, child, _clsid, _state,
             _ctime, _mtime, start, size) = _DIR_ENTRY.unpack_from(raw, sid * 128)
            if type_ not in (TYPE_STORAGE, TYPE_STREAM, TYPE_ROOT):
                self.entries.append(None)
                continue
            n = max(0, min(name_len, 64) - 2)
            if major == 3:
                size &= 0xFFFFFFFF      # the high half is not defined in version 3
            self.entries.append(DirEntry(
                sid, name[:n].decode("utf-16-le", "replace"), type_, left, right,
                child, start, size))
        if not self.entries or self.entries[0] is None or self.entries[0].type != TYPE_ROOT:
            raise MsiError("the compound file has no root entry")
        root = self.entries[0]

        # The mini stream (in the root's regular sectors) and its FAT.
        self._minifat = _u32_array(
            self._read_chain(first_minifat, self._fat, self.sector_size)
            if n_minifat and first_minifat <= MAXREGSECT else b"")
        self._mini_runs = self._runs(root.start, self._fat) if root.size else _Runs()
        self._mini_size = root.size

    # Sectors and chains.

    def _sector(self, sect):
        if sect > MAXREGSECT or sect >= self._max_sector:
            raise MsiError("a sector number past the end of the file")
        self._f.seek((sect + 1) << self.sector_shift)
        data = self._f.read(self.sector_size)
        if len(data) != self.sector_size:
            raise MsiError("the file ends inside a sector")
        return data

    def _runs(self, start, fat):
        runs = _Runs()
        sect, limit = start, len(fat)
        while sect != ENDOFCHAIN:
            if sect >= limit:
                raise MsiError("a sector chain leaves the allocation table")
            runs.add(sect)
            if runs.sectors > limit:
                raise MsiError("a sector chain loops")
            sect = fat[sect]
        return runs

    def _read_chain(self, start, fat, unit):
        """A whole (small) chain's bytes: the FAT's or the directory's."""
        if start == ENDOFCHAIN or start > MAXREGSECT:
            return b""
        out = bytearray()
        runs = self._runs(start, fat)
        for phys, count in zip(runs.physical, runs.count):
            for s in range(phys, phys + count):
                out += self._sector(s)
        return bytes(out)

    # Entries.

    def children(self, entry=None):
        """The entries directly inside storage `entry` (the root without one)."""
        entry = entry or self.entries[0]
        out, stack, seen = [], [entry.child], set()
        while stack:
            sid = stack.pop()
            if sid == NOSTREAM or sid in seen:
                continue
            if sid >= len(self.entries):
                raise MsiError("a directory link points past the directory")
            seen.add(sid)
            e = self.entries[sid]
            if e is None:
                continue
            out.append(e)
            stack.append(e.left)
            stack.append(e.right)
        return out

    def open_entry(self, entry):
        """A seekable, read-only binary file object over a stream."""
        if entry.type != TYPE_STREAM:
            raise MsiError("%r is not a stream" % entry.name)
        if entry.size < self.mini_cutoff:
            return io.BytesIO(self._read_mini(entry.start, entry.size))
        runs = self._runs(entry.start, self._fat)
        if runs.sectors * self.sector_size < entry.size:
            raise MsiError("stream %r is shorter than its size" % entry.name)
        raw = _StreamReader(self._f, runs, self.sector_shift, entry.size)
        return io.BufferedReader(raw, buffer_size=256 * 1024)

    def read_entry(self, entry, limit=64 * 1024 * 1024):
        """A whole stream's bytes (for the database tables, never the cabinet)."""
        if entry.size > limit:
            raise MsiError("stream %r is too big to read whole" % entry.name)
        with self.open_entry(entry) as s:
            data = s.read(entry.size)
        if len(data) != entry.size:
            raise MsiError("stream %r is cut short" % entry.name)
        return data

    def _read_mini(self, start, size):
        if size == 0:
            return b""
        out = bytearray()
        sect, n = start, 0
        mss = self.mini_sector_size
        limit = len(self._minifat)
        while sect != ENDOFCHAIN and len(out) < size:
            if sect >= limit:
                raise MsiError("a mini sector chain leaves its table")
            pos = sect * mss
            if pos + mss > self._mini_size + mss:
                raise MsiError("a mini sector past the mini stream")
            out += self._mini_read(pos, mss)
            n += 1
            if n > limit:
                raise MsiError("a mini sector chain loops")
            sect = self._minifat[sect]
        if len(out) < size:
            raise MsiError("a small stream is cut short")
        return bytes(out[:size])

    def _mini_read(self, pos, n):
        shift = self.sector_shift
        out = bytearray()
        while n > 0:
            phys, left = self._mini_runs.locate(pos >> shift)
            within = pos & (self.sector_size - 1)
            take = min(n, (left << shift) - within)
            self._f.seek(((phys + 1) << shift) + within)
            chunk = self._f.read(take)
            if len(chunk) != take:
                raise MsiError("the file ends inside the mini stream")
            out += chunk
            pos += take
            n -= take
        return bytes(out)


class _StreamReader(io.RawIOBase):
    """A big stream, read sector run by sector run on demand."""

    def __init__(self, f, runs, shift, size):
        super().__init__()
        self._f = f
        self._runs = runs
        self._shift = shift
        self._size = size
        self._pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, pos, whence=0):
        if whence == 1:
            pos += self._pos
        elif whence == 2:
            pos += self._size
        if pos < 0:
            raise ValueError("negative seek position")
        self._pos = pos
        return pos

    def readinto(self, b):
        n = min(len(b), self._size - self._pos)
        if n <= 0:
            return 0
        shift = self._shift
        phys, left = self._runs.locate(self._pos >> shift)
        within = self._pos & ((1 << shift) - 1)
        n = min(n, (left << shift) - within)
        self._f.seek(((phys + 1) << shift) + within)
        view = memoryview(b)[:n]
        got = self._f.readinto(view)
        if not got:
            raise MsiError("the file ends inside a stream")
        self._pos += got
        return got


# ── The installer database ──────────────────────────────────────────────────

_MIME = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"
TABLE_PREFIX = "䡀"


def decode_stream_name(name):
    """(name, is_table) of a stream name as Windows Installer packs it."""
    out = []
    is_table = name.startswith(TABLE_PREFIX)
    if is_table:
        name = name[1:]
    for ch in name:
        c = ord(ch)
        if 0x3800 <= c < 0x4800:
            c -= 0x3800
            out.append(_MIME[c & 0x3F])
            out.append(_MIME[(c >> 6) & 0x3F])
        elif 0x4800 <= c < 0x4840:
            out.append(_MIME[c - 0x4800])
        else:
            out.append(ch)
    return "".join(out), is_table


def encode_stream_name(name, table=False):
    """The packed form of `name` (decode_stream_name's inverse), for tests
    that build a package. Characters outside [0-9A-Za-z._] stay as they are."""
    out = [TABLE_PREFIX] if table else []
    i = 0
    while i < len(name):
        a = _MIME.find(name[i])
        if a < 0:
            out.append(name[i])
            i += 1
            continue
        b = _MIME.find(name[i + 1]) if i + 1 < len(name) else -1
        if b >= 0:
            out.append(chr(0x3800 + a + (b << 6)))
            i += 2
        else:
            out.append(chr(0x4800 + a))
            i += 1
    return "".join(out)


# Column types (msidefs.h / Wine's msipriv.h).
COL_VALID = 0x0100
COL_LOCALIZABLE = 0x0200
COL_STRING = 0x0800
COL_NULLABLE = 0x1000
COL_KEY = 0x2000
COL_TEMPORARY = 0x4000

#: The two tables that describe the others have a fixed layout.
_COLUMNS_COLS = [("Table", COL_VALID | COL_STRING | COL_KEY | 64),
                 ("Number", COL_VALID | COL_KEY | 2),
                 ("Name", COL_VALID | COL_STRING | 64),
                 ("Type", COL_VALID | 2)]


def parse_string_pool(pool, data):
    """(strings, bytes per string id, code page) from the raw _StringPool and
    _StringData streams. strings[0] is None; ids index the list."""
    if len(pool) < 4:
        return [None], 2, 0
    words = array("H")
    words.frombytes(pool[:len(pool) & ~3])
    if sys.byteorder == "big":          # pragma: no cover
        words.byteswap()
    codepage = words[0] | ((words[1] & 0x7FFF) << 16)
    strref = 3 if words[1] & 0x8000 else 2
    encoding = _encoding(codepage)
    count = len(words) // 2
    strings = [None]
    offset, i = 0, 1
    while i < count:
        length, refs = words[i * 2], words[i * 2 + 1]
        if length == 0 and refs == 0:
            strings.append(None)        # an unused id
            i += 1
            continue
        if length == 0:
            # A string of 64 KiB or more: this entry is a marker, the next
            # one holds the length's low word, its refcount the high one.
            if i + 1 >= count:
                break
            length = words[i * 2 + 2] | (words[i * 2 + 3] << 16)
            i += 2
        else:
            i += 1
        if offset + length > len(data):
            raise MsiError("the string pool is damaged")
        strings.append(data[offset:offset + length].decode(encoding, "replace"))
        offset += length
    return strings, strref, codepage


def _encoding(codepage):
    if codepage in (0, 1252):
        return "cp1252"
    if codepage == 65001:
        return "utf-8"
    try:
        enc = "cp%d" % codepage
        "".encode(enc)
        return enc
    except LookupError:
        return "cp1252"


def _is_binary(col_type):
    """A binary column (a stream): "string" and "valid" and nothing else;
    a real string column also has bit 0x400 set."""
    return (col_type & ~COL_NULLABLE) == (COL_STRING | COL_VALID)


def _col_width(col_type, strref):
    if _is_binary(col_type):
        return 2
    if col_type & COL_STRING:
        return strref
    return 2 if (col_type & 0xFF) <= 2 else 4


def parse_table(raw, columns, strings, strref):
    """The rows of a table stream: a list of {column name: value}, a value
    being a str, an int or None. `columns` is [(name, type)] in order."""
    widths = [_col_width(t, strref) for _n, t in columns]
    row_size = sum(widths)
    if not row_size:
        return []
    n_rows = len(raw) // row_size
    rows = [{} for _ in range(n_rows)]
    base = 0
    for (name, col_type), w in zip(columns, widths):
        binary = _is_binary(col_type)
        is_string = bool(col_type & COL_STRING) and not binary
        for r in range(n_rows):
            v = int.from_bytes(raw[base + r * w: base + r * w + w], "little")
            if is_string:
                val = strings[v] if 0 < v < len(strings) else None
            elif binary:
                val = v or None         # a stream reference, kept raw
            elif v == 0:
                val = None
            elif w == 2:
                val = v - 0x8000
            else:
                val = v - 0x80000000
            rows[r][name] = val
        base += n_rows * w
    return rows


class MsiFile:
    __slots__ = ("key", "path", "size", "sequence", "attributes")

    def __init__(self, key, path, size, sequence, attributes):
        self.key = key
        self.path = path            # relative, "/"-separated, long names
        self.size = size
        self.sequence = sequence
        self.attributes = attributes

    def __repr__(self):
        return "MsiFile(%r, %r)" % (self.key, self.path)


def long_name(name):
    """The long half of a "SHORT~1|Long Name" pair."""
    if name is None:
        return ""
    return name.split("|", 1)[1] if "|" in name else name


def target_dir_name(default_dir):
    """A Directory row's name on the target: DefaultDir is
    "target[:source]", each "short|long" or a single name; "." means the
    parent itself."""
    return long_name((default_dir or "").split(":", 1)[0])


class MsiPackage:
    """The database and streams of an .msi."""

    def __init__(self, f, close=None):
        self._close = close
        self.cfb = CompoundFile(f)
        self.streams = {}
        self.table_streams = {}
        for e in self.cfb.children():
            if e.type != TYPE_STREAM:
                continue
            name, is_table = decode_stream_name(e.name)
            (self.table_streams if is_table else self.streams)[name] = e
        if "_StringPool" not in self.table_streams or "_StringData" not in self.table_streams:
            raise MsiError("not an installer database (no string pool)")
        self.strings, self.strref, self.codepage = parse_string_pool(
            self.cfb.read_entry(self.table_streams["_StringPool"]),
            self.cfb.read_entry(self.table_streams["_StringData"]))
        self.columns = {}
        for row in self._rows("_Columns", _COLUMNS_COLS):
            if row["Table"] is None or row["Number"] is None or row["Name"] is None:
                continue
            self.columns.setdefault(row["Table"], []).append(
                (row["Number"], row["Name"], row["Type"] or 0))
        for cols in self.columns.values():
            cols.sort()

    @classmethod
    def open(cls, path):
        f = open(path, "rb")
        try:
            return cls(f, close=f.close)
        except Exception:
            f.close()
            raise

    def close(self):
        if self._close:
            self._close()
            self._close = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _rows(self, table, columns):
        entry = self.table_streams.get(table)
        if entry is None:
            return []
        return parse_table(self.cfb.read_entry(entry), columns, self.strings, self.strref)

    def table(self, name):
        """The rows of table `name` ([] when it is empty or absent)."""
        cols = self.columns.get(name)
        if not cols:
            return []
        return self._rows(name, [(n, t) for _num, n, t in cols
                                 if not t & COL_TEMPORARY])

    def directories(self):
        """{Directory key: relative target path}, "" for the root."""
        rows = {r["Directory"]: r for r in self.table("Directory") if r.get("Directory")}
        out = {}

        def resolve(key):
            parts, seen, d = [], set(), key
            while d is not None and d in rows and d not in seen:
                seen.add(d)
                row = rows[d]
                parent = row.get("Directory_Parent")
                if parent is None or parent == d:
                    break               # the root (TARGETDIR): no name of its own
                name = target_dir_name(row.get("DefaultDir"))
                if name and name != ".":
                    parts.append(name)
                d = parent
            return "/".join(reversed(parts))
        for key in rows:
            out[key] = resolve(key)
        return out

    def files(self):
        """{File key: MsiFile} with each file's path relative to the
        installation's root."""
        dirs = self.directories()
        comps = {r["Component"]: r.get("Directory_") for r in self.table("Component")
                 if r.get("Component")}
        out = {}
        for r in self.table("File"):
            key = r.get("File")
            if not key:
                continue
            folder = dirs.get(comps.get(r.get("Component_")), "")
            name = long_name(r.get("FileName"))
            path = folder + "/" + name if folder else name
            out[key] = MsiFile(key, path, r.get("FileSize"), r.get("Sequence"),
                               r.get("Attributes"))
        return out

    def cabinets(self):
        """Names of the cabinet streams inside the package, in disk order.
        A Media row's Cabinet "#name" means the stream "name"; without a
        Media table, every stream that starts like a cabinet."""
        names = []
        for r in sorted(self.table("Media"), key=lambda r: r.get("DiskId") or 0):
            cab = r.get("Cabinet") or ""
            if cab.startswith("#") and cab[1:] in self.streams and cab[1:] not in names:
                names.append(cab[1:])
        if names:
            return names
        for name, entry in sorted(self.streams.items()):
            if entry.size >= 36:
                with self.cfb.open_entry(entry) as s:
                    if s.read(4) == b"MSCF":
                        names.append(name)
        return names

    def open_stream(self, name):
        """A seekable binary file object over stream `name` (not a table)."""
        entry = self.streams.get(name)
        if entry is None:
            raise MsiError("no stream %r in the package" % name)
        return self.cfb.open_entry(entry)


def is_compound_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == SIGNATURE
    except OSError:
        return False


__all__ = ["CompoundFile", "MsiPackage", "MsiFile", "MsiError", "decode_stream_name",
           "encode_stream_name", "parse_string_pool", "parse_table", "is_compound_file",
           "SIGNATURE"]
