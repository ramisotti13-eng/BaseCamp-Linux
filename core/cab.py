"""Reading a Microsoft cabinet (.cab) and taking single files out of it.

Mountain ships Base Camp as a Windows Installer package whose files sit in
one cabinet stream inside the .msi (core/msi.py finds it). This module reads
such a cabinet from any seekable binary file object and writes the files it
is asked for, and only those, to disk:

    cab = Cabinet(stream)
    cab.files                        # [CabFile(name, size, folder, offset)]
    cab.extract({"filA1B2": "/tmp/x/mkd_pro.png"}, progress=..., cancel=...)

The format (MS-CAB): a header, one CFFOLDER per folder, one CFFILE per file,
then per folder a run of CFDATA blocks. A folder is one compressed stream:
its files lie end to end in it, so a file is reached by decompressing its
folder from the start up to the end of the file. Blocks are read one at a
time (at most 64 KiB each), so a cabinet of any size is never in memory.

Compression: none and MSZIP. An MSZIP block is "CK" and a raw deflate
stream of at most 32 KiB, whose back references may reach into the
previous block's output; that history is handed to zlib as the dictionary.
Quantum and LZX are refused with CabError.

A block's checksum, when the cabinet has one, is checked (the algorithm of
libmspack's cabd_checksum). The whole module is pure Python: zlib, struct.
"""
import struct
import zlib

SIGNATURE = b"MSCF"

COMP_NONE = 0
COMP_MSZIP = 1
COMP_NAMES = {0: "none", 1: "MSZIP", 2: "Quantum", 3: "LZX"}

#: Header flags.
_PREV_CABINET = 0x0001
_NEXT_CABINET = 0x0002
_RESERVE_PRESENT = 0x0004

#: CFFILE.iFolder values for files that continue in another cabinet.
_CONTINUED_FROM_PREV = 0xFFFD
_CONTINUED_TO_NEXT = 0xFFFE
_CONTINUED_PREV_AND_NEXT = 0xFFFF

#: _A_NAME_IS_UTF: the file name is UTF-8 rather than the local code page.
_NAME_IS_UTF = 0x80

#: An MSZIP block never holds more than this, and deflate never looks back
#: further: the history kept between blocks.
BLOCK = 32768

_HEADER = struct.Struct("<4sIIIIIBBHHHHH")      # 36 bytes
_FOLDER = struct.Struct("<IHH")                 # 8 bytes
_FILE = struct.Struct("<IIHHHH")                # 16 bytes, then the name
_DATA = struct.Struct("<IHH")                   # 8 bytes

#: Limits that only a broken or hostile cabinet reaches.
MAX_FILES = 65535
MAX_NAME = 1024


class CabError(Exception):
    """The cabinet is broken, or uses something this reader does not do."""


class CabFile:
    __slots__ = ("name", "size", "folder", "offset", "attribs")

    def __init__(self, name, size, folder, offset, attribs):
        self.name = name
        self.size = size
        self.folder = folder
        self.offset = offset        # where the file starts in its folder
        self.attribs = attribs

    def __repr__(self):
        return "CabFile(%r, %d bytes, folder %d @ %d)" % (
            self.name, self.size, self.folder, self.offset)


class CabFolder:
    __slots__ = ("index", "data_offset", "blocks", "compression")

    def __init__(self, index, data_offset, blocks, compression):
        self.index = index
        self.data_offset = data_offset
        self.blocks = blocks
        self.compression = compression

    @property
    def compression_name(self):
        return COMP_NAMES.get(self.compression, str(self.compression))


def checksum(data, seed=0):
    """The cabinet checksum of `data` (libmspack's cabd_checksum).

    The 32-bit little-endian words are XORed together, the 1-3 bytes left
    over are XORed in as a big-endian number. The words are folded as one
    big integer, which is far quicker in Python than a loop over them.
    """
    n = len(data)
    whole = n & ~3
    x = int.from_bytes(data[:whole], "little") if whole else 0
    bits = whole * 8
    while bits > 32:
        half = ((bits // 32 + 1) // 2) * 32
        x = (x & ((1 << half) - 1)) ^ (x >> half)
        bits = half
    tail = 0
    for b in data[whole:]:
        tail = (tail << 8) | b
    return (seed ^ x ^ tail) & 0xFFFFFFFF


def _read_exact(f, n, what):
    data = f.read(n)
    if len(data) != n:
        raise CabError("the cabinet ends inside %s" % what)
    return data


def _read_cstring(f, what):
    out = bytearray()
    while True:
        c = f.read(1)
        if not c:
            raise CabError("the cabinet ends inside %s" % what)
        if c == b"\0":
            return bytes(out)
        out += c
        if len(out) > MAX_NAME:
            raise CabError("%s is too long" % what)


class Cabinet:
    """The directory of a cabinet read from the seekable file object `f`,
    whose offset 0 is the cabinet's first byte."""

    def __init__(self, f):
        self._f = f
        f.seek(0)
        hdr = _read_exact(f, _HEADER.size, "the header")
        (sig, _r1, self.size, _r2, coff_files, _r3, minor, major,
         n_folders, n_files, flags, _set_id, _icab) = _HEADER.unpack(hdr)
        if sig != SIGNATURE:
            raise CabError("not a cabinet")
        if (major, minor) != (1, 3):
            raise CabError("unknown cabinet version %d.%d" % (major, minor))
        if n_files > MAX_FILES:
            raise CabError("too many files")
        self.flags = flags
        self._folder_reserve = self._data_reserve = 0
        if flags & _RESERVE_PRESENT:
            cb_header, self._folder_reserve, self._data_reserve = struct.unpack(
                "<HBB", _read_exact(f, 4, "the header"))
            f.seek(cb_header, 1)
        if flags & _PREV_CABINET:
            _read_cstring(f, "the previous cabinet's name")
            _read_cstring(f, "the previous disk's name")
        if flags & _NEXT_CABINET:
            _read_cstring(f, "the next cabinet's name")
            _read_cstring(f, "the next disk's name")

        self.folders = []
        for i in range(n_folders):
            off, blocks, comp = _FOLDER.unpack(_read_exact(f, _FOLDER.size, "a folder entry"))
            if self._folder_reserve:
                f.seek(self._folder_reserve, 1)
            self.folders.append(CabFolder(i, off, blocks, comp & 0x000F))

        f.seek(coff_files)
        self.files = []
        for _ in range(n_files):
            size, offset, folder, _date, _time, attribs = _FILE.unpack(
                _read_exact(f, _FILE.size, "a file entry"))
            raw = _read_cstring(f, "a file name")
            name = raw.decode("utf-8" if attribs & _NAME_IS_UTF else "cp1252",
                              "replace")
            if folder == _CONTINUED_TO_NEXT:
                folder = n_folders - 1
            elif folder in (_CONTINUED_FROM_PREV, _CONTINUED_PREV_AND_NEXT):
                folder = -1             # starts in another cabinet: not here
            elif folder >= n_folders:
                raise CabError("file %r names a folder that does not exist" % name)
            self.files.append(CabFile(name, size, folder, offset, attribs))
        self._by_name = {}
        for cf in self.files:
            self._by_name.setdefault(cf.name, cf)

    def find(self, name):
        """The CabFile called `name`, or None."""
        return self._by_name.get(name)

    # ── Extraction ─────────────────────────────────────────────────────────

    def extract(self, wanted, progress=None, cancel=None):
        """Write the files named in `wanted` ({name: destination path}).

        Each folder that holds one of them is decompressed from its first
        block up to the end of the last wanted file in it, and no further.
        `progress(done, total)` is called after each block, in uncompressed
        bytes of the folders' parts that have to be gone through;
        `cancel()` returning true stops between blocks with CabError.
        Returns {name: size written}. Raises CabError (a name that is not
        in the cabinet, a file that continues in another cabinet, a damaged
        block, a compression this module does not read).
        """
        jobs = {}
        for name, dest in wanted.items():
            cf = self.find(name)
            if cf is None:
                raise CabError("%s is not in the cabinet" % name)
            if cf.folder < 0:
                raise CabError("%s starts in another cabinet" % name)
            jobs.setdefault(cf.folder, []).append((cf, dest))
        total = sum(max(cf.offset + cf.size for cf, _ in files)
                    for files in jobs.values())
        state = {"done": 0}

        def advance(n):
            state["done"] += n
            if progress:
                progress(state["done"], total)

        written = {}
        for index in sorted(jobs, key=lambda i: self.folders[i].data_offset):
            written.update(self._extract_folder(self.folders[index], jobs[index],
                                                advance, cancel))
        return written

    def _extract_folder(self, folder, files, advance, cancel):
        if folder.compression not in (COMP_NONE, COMP_MSZIP):
            raise CabError("folder %d is compressed with %s, which is not supported"
                           % (folder.index, folder.compression_name))
        files = sorted(files, key=lambda x: x[0].offset)
        end = max(cf.offset + cf.size for cf, _ in files)
        outs = {}                       # name -> [file object, bytes written]
        pos = 0                         # uncompressed offset of the block
        f = self._f
        f.seek(folder.data_offset)
        history = b""
        try:
            for cf, dest in files:
                if cf.size == 0:
                    open(dest, "wb").close()
                    outs[cf.name] = [None, 0]
            block = 0
            while pos < end:
                if cancel is not None and cancel():
                    raise CabError("cancelled")
                if block >= folder.blocks:
                    raise CabError("folder %d ends before its files do" % folder.index)
                csum, cb_data, cb_uncomp = _DATA.unpack(
                    _read_exact(f, _DATA.size, "a data block header"))
                if self._data_reserve:
                    f.seek(self._data_reserve, 1)
                data = _read_exact(f, cb_data, "a data block")
                if csum and checksum(_DATA.pack(0, cb_data, cb_uncomp)[4:],
                                     checksum(data)) != csum:
                    raise CabError("block %d of folder %d is damaged (checksum)"
                                   % (block, folder.index))
                if folder.compression == COMP_NONE:
                    out = data
                else:
                    out = _inflate_block(data, history, cb_uncomp)
                    history = (history + out)[-BLOCK:]
                if len(out) != cb_uncomp:
                    raise CabError("block %d of folder %d has the wrong size"
                                   % (block, folder.index))
                self._deliver(out, pos, files, outs)
                pos += len(out)
                block += 1
                advance(len(out))
        finally:
            for fo, _n in outs.values():
                if fo is not None:
                    fo.close()
        result = {}
        for cf, _dest in files:
            got = outs.get(cf.name, [None, 0])[1]
            if got != cf.size:
                raise CabError("%s came out with %d of %d bytes" % (cf.name, got, cf.size))
            result[cf.name] = got
        return result

    @staticmethod
    def _deliver(out, pos, files, outs):
        """Hand the part of block `out` (at folder offset `pos`) that
        belongs to each wanted file to that file."""
        block_end = pos + len(out)
        for cf, dest in files:
            start, stop = cf.offset, cf.offset + cf.size
            if stop <= pos or start >= block_end or cf.size == 0:
                continue
            entry = outs.get(cf.name)
            if entry is None:
                entry = outs[cf.name] = [open(dest, "wb"), 0]
            a = max(start, pos) - pos
            b = min(stop, block_end) - pos
            entry[0].write(out[a:b])
            entry[1] += b - a
            if entry[1] == cf.size:
                entry[0].close()
                entry[0] = None


def _inflate_block(data, history, expected):
    """One MSZIP block: "CK", then raw deflate that may refer back into
    `history` (the previous blocks' last 32 KiB)."""
    if data[:2] != b"CK":
        raise CabError("an MSZIP block without its CK signature")
    try:
        d = zlib.decompressobj(-15, zdict=history) if history else zlib.decompressobj(-15)
        # Never more than one byte past what the block says it holds: a
        # block that inflates further is broken, and is not inflated on.
        return d.decompress(data[2:], expected + 1)
    except zlib.error as e:
        raise CabError("an MSZIP block does not inflate: %s" % e) from None


def list_files(path):
    """[(name, size, folder)] of the cabinet file at `path` (a helper for
    looking at one by hand)."""
    with open(path, "rb") as f:
        return [(cf.name, cf.size, cf.folder) for cf in Cabinet(f).files]


__all__ = ["Cabinet", "CabFile", "CabFolder", "CabError", "checksum", "list_files"]
