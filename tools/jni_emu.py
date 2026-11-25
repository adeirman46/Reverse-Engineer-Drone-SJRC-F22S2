#!/usr/bin/env python3
"""
jni_emu.py - run JNI functions from an Android arm64 .so inside a Unicorn sandbox.

The library's code never runs natively: Unicorn emulates the CPU, and the code only sees
the few libc and JNIEnv functions implemented below. Java objects are fake: a "bean" is
a Python dict of field values that the native code reads through GetFieldID/Get<Type>Field.

Typical use:
    emu = JniEmu("native/libLGDataUtils.so")
    cls = emu.new_class("com/vison/macrochip/sdk/LGDataUtils")
    bean = emu.new_object("com/vison/macrochip/mode/LGControlHyBean", {"rocker1": 128})
    out = emu.array_bytes(emu.call_jni("Java_..._convertHyControl", cls, bean))
"""
import struct

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm64_const as A

PAGE = 0x1000
BASE = 0x1000_0000
IMPORT_STUB = 0x7000_0000  # one 'ret' per imported libc symbol
JNI_STUB = 0x7100_0000     # one 'ret' per JNIEnv function
JNI_TABLE = 0x7200_0000    # JNINativeInterface: function pointers
JNI_ENV = 0x7300_0000      # JNIEnv*: points at JNI_TABLE
TLS = 0x7400_0000
HEAP, HEAP_SIZE = 0x8000_0000, 0x0400_0000
STACK, STACK_SIZE = 0x9000_0000, 0x0010_0000
RETURN_TRAP = 0xA000_0000
HANDLE_BASE = 0x7E00_0000_0000  # fake JNI references, never dereferenced by native code
RET = struct.pack("<I", 0xD65F03C0)
JNI_SLOTS = 240

R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027

# JNINativeInterface indices (jni.h order) for the functions implemented here.
JNI_NAMES = {
    6: "FindClass", 15: "ExceptionOccurred", 17: "ExceptionClear", 21: "NewGlobalRef",
    22: "DeleteGlobalRef", 23: "DeleteLocalRef", 25: "NewLocalRef", 27: "AllocObject",
    28: "NewObject", 31: "GetObjectClass", 33: "GetMethodID", 94: "GetFieldID",
    95: "GetObjectField", 96: "GetBooleanField", 97: "GetByteField", 98: "GetCharField",
    99: "GetShortField", 100: "GetIntField", 101: "GetLongField", 102: "GetFloatField",
    103: "GetDoubleField", 104: "SetObjectField", 105: "SetBooleanField", 106: "SetByteField",
    107: "SetCharField", 108: "SetShortField", 109: "SetIntField", 110: "SetLongField",
    111: "SetFloatField", 112: "SetDoubleField", 167: "NewStringUTF", 171: "GetArrayLength",
    176: "NewByteArray", 184: "GetByteArrayElements", 192: "ReleaseByteArrayElements",
    200: "GetByteArrayRegion", 208: "SetByteArrayRegion", 222: "GetPrimitiveArrayCritical",
    223: "ReleasePrimitiveArrayCritical", 228: "ExceptionCheck",
    172: "NewObjectArray", 173: "GetObjectArrayElement", 174: "SetObjectArrayElement",
    179: "NewIntArray", 187: "GetIntArrayElements", 195: "ReleaseIntArrayElements",
    203: "GetIntArrayRegion", 211: "SetIntArrayRegion",
}
INT_SIGS = {"Z": 1, "B": 1, "C": 2, "S": 2, "I": 4, "J": 8}


class EmuError(Exception):
    pass


class JniEmu:
    def __init__(self, path, verbose=False):
        self.verbose = verbose
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.handles = {}       # handle -> python object
        self.field_ids = {}     # field id -> (name, sig)
        self.field_log = []     # (name, sig) in the order the native code asked for them
        self.jni_log = []       # names of JNI calls made during the last call_jni
        self.pinned = {}        # emulator address -> array handle (Get/Release*Elements)
        self.heap_ptr = HEAP
        self.alloc_sizes = {}
        with open(path, "rb") as f:
            self.elf = ELFFile(f)
            self._load()
        self._setup_runtime()
        self._run_initializers()

    # ---------- memory helpers ----------
    def r64(self, a): return struct.unpack("<Q", self.uc.mem_read(a, 8))[0]
    def w64(self, a, v): self.uc.mem_write(a, struct.pack("<Q", v & (2**64 - 1)))
    def reg(self, n): return self.uc.reg_read(getattr(A, f"UC_ARM64_REG_X{n}"))
    def set_ret(self, v): self.uc.reg_write(A.UC_ARM64_REG_X0, v & (2**64 - 1))

    def cstr(self, a, limit=4096):
        out = bytearray()
        while len(out) < limit:
            chunk = self.uc.mem_read(a + len(out), 64)
            i = chunk.find(b"\0")
            if i >= 0:
                return (out + chunk[:i]).decode("utf-8", "replace")
            out += chunk
        return out.decode("utf-8", "replace")

    def malloc(self, n):
        n = max(16, (n + 15) & ~15)
        p = self.heap_ptr
        if p + n > HEAP + HEAP_SIZE:
            raise EmuError("emulated heap exhausted")
        self.heap_ptr += n
        self.uc.mem_write(p, b"\0" * n)
        self.alloc_sizes[p] = n
        return p

    def _handle(self, obj):
        h = HANDLE_BASE + 16 * (len(self.handles) + 1)
        self.handles[h] = obj
        return h

    # ---------- loading ----------
    def _load(self):
        segs = [s for s in self.elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        lo = min(s["p_vaddr"] for s in segs) & ~(PAGE - 1)
        hi = max(s["p_vaddr"] + s["p_memsz"] for s in segs)
        hi = (hi + PAGE - 1) & ~(PAGE - 1)
        self.uc.mem_map(BASE + lo, hi - lo)
        for s in segs:
            self.uc.mem_write(BASE + s["p_vaddr"], s.data())

        dynsym = self.elf.get_section_by_name(".dynsym")
        self.symbols = {sym.name: BASE + sym["st_value"] for sym in dynsym.iter_symbols()
                        if sym["st_shndx"] != "SHN_UNDEF" and sym["st_value"]}
        self.import_names = []
        import_addr = {}
        for sec in self.elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            symtab = self.elf.get_section(sec["sh_link"])
            for rel in sec.iter_relocations():
                t, off = rel["r_info_type"], BASE + rel["r_offset"]
                add = rel["r_addend"] if rel.is_RELA() else 0
                if t == R_RELATIVE:
                    self.w64(off, BASE + add)
                elif t in (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT):
                    sym = symtab.get_symbol(rel["r_info_sym"])
                    if sym["st_shndx"] != "SHN_UNDEF":
                        val = BASE + sym["st_value"]
                    else:
                        if sym.name not in import_addr:
                            import_addr[sym.name] = IMPORT_STUB + 4 * len(self.import_names)
                            self.import_names.append(sym.name)
                        val = import_addr[sym.name]
                    self.w64(off, val + (add if t == R_ABS64 else 0))
                elif t != 0:
                    raise EmuError(f"unsupported relocation type {t}")
        dyn = next((s for s in self.elf.iter_sections() if isinstance(s, DynamicSection)), None)
        self.dyn_tags = {t.entry.d_tag: t.entry.d_val for t in dyn.iter_tags()} if dyn else {}

    def _setup_runtime(self):
        uc = self.uc
        n_imp = max(1, len(self.import_names))
        uc.mem_map(IMPORT_STUB, (4 * n_imp + PAGE - 1) & ~(PAGE - 1))
        uc.mem_write(IMPORT_STUB, RET * n_imp)
        uc.hook_add(UC_HOOK_CODE, self._on_import, begin=IMPORT_STUB, end=IMPORT_STUB + 4 * n_imp - 1)

        uc.mem_map(JNI_STUB, PAGE)
        uc.mem_write(JNI_STUB, RET * JNI_SLOTS)
        uc.hook_add(UC_HOOK_CODE, self._on_jni, begin=JNI_STUB, end=JNI_STUB + 4 * JNI_SLOTS - 1)
        uc.mem_map(JNI_TABLE, PAGE * 2)
        for i in range(JNI_SLOTS):
            self.w64(JNI_TABLE + 8 * i, JNI_STUB + 4 * i)
        uc.mem_map(JNI_ENV, PAGE)
        self.w64(JNI_ENV, JNI_TABLE)

        uc.mem_map(TLS, PAGE)
        self.w64(TLS + 0x28, 0x5AFE_C0DE_5AFE_C0DE)  # bionic stack-guard slot
        uc.reg_write(A.UC_ARM64_REG_TPIDR_EL0, TLS)
        uc.reg_write(A.UC_ARM64_REG_CPACR_EL1, 0x300000)  # enable FP/SIMD
        uc.mem_map(HEAP, HEAP_SIZE)
        uc.mem_map(STACK, STACK_SIZE)
        uc.mem_map(RETURN_TRAP, PAGE)
        uc.mem_write(RETURN_TRAP, RET)

    def _run_initializers(self):
        tags = self.dyn_tags
        funcs = []
        if "DT_INIT" in tags:
            funcs.append(BASE + tags["DT_INIT"])
        if "DT_INIT_ARRAY" in tags:
            arr = BASE + tags["DT_INIT_ARRAY"]
            funcs += [self.r64(arr + 8 * i) for i in range(tags.get("DT_INIT_ARRAYSZ", 0) // 8)]
        for f in funcs:
            if f in (0, 2**64 - 1, BASE):
                continue
            try:
                self.call(f, [])
            except Exception as e:  # initializers are best-effort
                if self.verbose:
                    print(f"[init] {f:#x} failed: {e}")

    # ---------- calling ----------
    def call(self, addr, args, max_insns=20_000_000):
        if len(args) > 8:
            raise EmuError("more than 8 integer arguments not supported")
        for i, a in enumerate(args):
            self.uc.reg_write(getattr(A, f"UC_ARM64_REG_X{i}"), a & (2**64 - 1))
        self.uc.reg_write(A.UC_ARM64_REG_SP, STACK + STACK_SIZE - 0x1000)
        self.uc.reg_write(A.UC_ARM64_REG_LR, RETURN_TRAP)
        self.uc.emu_start(addr, RETURN_TRAP, count=max_insns)
        if self.uc.reg_read(A.UC_ARM64_REG_PC) != RETURN_TRAP:
            raise EmuError(f"function did not return (pc={self.uc.reg_read(A.UC_ARM64_REG_PC):#x})")
        return self.reg(0)

    def call_jni(self, symbol, *java_args):
        if symbol not in self.symbols:
            raise EmuError(f"symbol not found: {symbol}")
        self.jni_log = []
        return self.call(self.symbols[symbol], [JNI_ENV, *java_args])

    # ---------- fake Java objects ----------
    def new_class(self, name):
        return self._handle({"kind": "class", "name": name})

    def new_object(self, class_name, fields=None):
        return self._handle({"kind": "object", "cls": class_name, "fields": dict(fields or {})})

    def new_byte_array(self, data):
        return self._handle({"kind": "array", "esize": 1, "data": bytearray(data)})

    def object_fields(self, h):
        return self.handles[h]["fields"]

    def array_bytes(self, h):
        if h == 0:
            return None
        return bytes(self.handles[h]["data"])

    def array_values(self, h):
        """Elements of a fake Java primitive array (int[] as signed 32-bit)."""
        a = self.handles[h]
        if a.get("esize", 1) == 4:
            return list(struct.unpack(f"<{len(a['data']) // 4}i", bytes(a["data"])))
        return list(a["data"])

    # ---------- libc stubs ----------
    def _on_import(self, uc, addr, size, _):
        name = self.import_names[(addr - IMPORT_STUB) // 4]
        x = self.reg
        if name == "malloc":
            self.set_ret(self.malloc(x(0)))
        elif name == "posix_memalign":
            self.w64(x(0), self.malloc(x(2))); self.set_ret(0)
        elif name == "realloc":
            old, n = x(0), x(1)
            p = self.malloc(n)
            if old:
                uc.mem_write(p, bytes(uc.mem_read(old, min(n, self.alloc_sizes.get(old, n)))))
            self.set_ret(p)
        elif name == "free":
            pass
        elif name in ("memcpy", "memmove"):
            if x(2):
                uc.mem_write(x(0), bytes(uc.mem_read(x(1), x(2))))
            self.set_ret(x(0))
        elif name == "memset":
            if x(2):
                uc.mem_write(x(0), bytes([x(1) & 0xFF]) * x(2))
            self.set_ret(x(0))
        elif name == "memcmp":
            a, b = bytes(uc.mem_read(x(0), x(2))), bytes(uc.mem_read(x(1), x(2)))
            self.set_ret(0 if a == b else (1 if a > b else -1))
        elif name == "strlen":
            self.set_ret(len(self.cstr(x(0)).encode()))
        elif name in ("strcmp", "strncmp"):
            a, b = self.cstr(x(0)), self.cstr(x(1))
            if name == "strncmp":
                a, b = a[: x(2)], b[: x(2)]
            self.set_ret(0 if a == b else (1 if a > b else -1))
        elif name in ("__stack_chk_fail", "abort"):
            raise EmuError(f"native code called {name}")
        else:  # pthread_*, __cxa_atexit, logging, printf family, ...: succeed and do nothing
            if self.verbose:
                print(f"[libc] {name} (ignored)")
            self.set_ret(0)

    # ---------- JNIEnv ----------
    def _on_jni(self, uc, addr, size, _):
        idx = (addr - JNI_STUB) // 4
        name = JNI_NAMES.get(idx)
        if name is None:
            raise EmuError(f"unimplemented JNIEnv function #{idx}")
        self.jni_log.append(name)
        x = self.reg
        H = self.handles
        if name == "FindClass":
            self.set_ret(self.new_class(self.cstr(x(1))))
        elif name == "GetObjectClass":
            self.set_ret(self.new_class(H[x(1)]["cls"]))
        elif name in ("ExceptionOccurred", "ExceptionCheck"):
            self.set_ret(0)
        elif name in ("ExceptionClear", "DeleteLocalRef", "DeleteGlobalRef"):
            pass
        elif name in ("NewGlobalRef", "NewLocalRef"):
            self.set_ret(x(1))
        elif name in ("AllocObject", "NewObject"):
            self.set_ret(self.new_object(H[x(1)]["name"]))
        elif name == "GetMethodID":
            self.set_ret(self._handle({"kind": "method", "name": self.cstr(x(2)), "sig": self.cstr(x(3))}))
        elif name == "GetFieldID":
            fname, sig = self.cstr(x(2)), self.cstr(x(3))
            fid = self._handle({"kind": "field"})
            self.field_ids[fid] = (fname, sig)
            self.field_log.append((fname, sig))
            self.set_ret(fid)
        elif name.startswith("Get") and name.endswith("Field"):
            fname, sig = self.field_ids[x(2)]
            v = H[x(1)]["fields"].get(fname, 0)
            if sig == "F":
                uc.reg_write(A.UC_ARM64_REG_S0, struct.unpack("<I", struct.pack("<f", float(v)))[0])
            elif sig == "D":
                uc.reg_write(A.UC_ARM64_REG_D0, struct.unpack("<Q", struct.pack("<d", float(v)))[0])
            else:
                self.set_ret(int(v))
        elif name.startswith("Set") and name.endswith("Field"):
            fname, sig = self.field_ids[x(2)]
            if sig == "F":
                v = struct.unpack("<f", struct.pack("<I", uc.reg_read(A.UC_ARM64_REG_S0) & 0xFFFFFFFF))[0]
            elif sig == "D":
                v = struct.unpack("<d", struct.pack("<Q", uc.reg_read(A.UC_ARM64_REG_D0)))[0]
            elif sig in INT_SIGS:
                bits = 8 * INT_SIGS[sig]
                v = x(3) & ((1 << bits) - 1)
                if sig != "C" and sig != "Z" and v >= 1 << (bits - 1):
                    v -= 1 << bits
            else:
                v = x(3)
            H[x(1)]["fields"][fname] = v
        elif name == "NewStringUTF":
            self.set_ret(self._handle({"kind": "string", "value": self.cstr(x(1))}))
        elif name in ("NewByteArray", "NewIntArray"):
            es = 1 if name == "NewByteArray" else 4
            self.set_ret(self._handle({"kind": "array", "esize": es, "data": bytearray(es * x(1))}))
        elif name == "NewObjectArray":
            self.set_ret(self._handle({"kind": "objarray", "items": [x(3)] * x(1)}))
        elif name == "GetObjectArrayElement":
            self.set_ret(H[x(1)]["items"][x(2)])
        elif name == "SetObjectArrayElement":
            H[x(1)]["items"][x(2)] = x(3)
        elif name == "GetArrayLength":
            a = H[x(1)]
            self.set_ret(len(a["items"]) if a["kind"] == "objarray" else len(a["data"]) // a.get("esize", 1))
        elif name in ("GetByteArrayElements", "GetIntArrayElements", "GetPrimitiveArrayCritical"):
            data = H[x(1)]["data"]
            p = self.malloc(len(data) + 1)
            uc.mem_write(p, bytes(data))
            self.pinned[p] = x(1)
            if x(2):
                uc.mem_write(x(2), b"\1")
            self.set_ret(p)
        elif name in ("ReleaseByteArrayElements", "ReleaseIntArrayElements", "ReleasePrimitiveArrayCritical"):
            arr, p, mode = x(1), x(2), x(3)
            if mode != 2:  # JNI_ABORT discards changes
                n = len(H[arr]["data"])
                H[arr]["data"][:] = uc.mem_read(p, n)
        elif name in ("GetByteArrayRegion", "GetIntArrayRegion"):
            arr, start, n, buf = x(1), x(2), x(3), x(4)
            es = H[arr].get("esize", 1)
            uc.mem_write(buf, bytes(H[arr]["data"][start * es:(start + n) * es]))
        elif name in ("SetByteArrayRegion", "SetIntArrayRegion"):
            arr, start, n, buf = x(1), x(2), x(3), x(4)
            es = H[arr].get("esize", 1)
            H[arr]["data"][start * es:(start + n) * es] = uc.mem_read(buf, n * es)
