# -*- coding: utf-8 -*-
"""z7_init_check_v35.py -- why does PSS/E 35 stop at start-up?

Run it with the same Python as z7_main_v35.py, from the study root:

    python.exe z7_init_check_v35.py

It changes nothing. It
  1. counts the .dll in Base\\ and Projects\\ by bitness (PSS/E 35 is 64-bit and
     cannot load a 32-bit PSS/E 34 dsusr.dll or vendor model DLL);
  2. lists the DLLs PSS/E ships in PSSBIN that another folder would supply first
     inside python.exe -- Python's own folder or System32 -- with both versions;
  3. starts PSS/E in an empty folder, one process per test, and records which
     DLLs that process had loaded, from where and in which version, when it
     started or died:
       1. as the study starts it;
       2. with PSSBIN first in Windows' DLL search (SetDllDirectory), as it is
          for the PSS/E GUI;
       3. with PSS/E's own runtime DLLs (Intel Fortran / OpenMP, Visual C++)
          loaded first, by full path;
       4. from a copy of python.exe that has the newest Visual C++ runtime
          beside it (only when there is one newer than Python's own);
     the first way that starts is tried again beside a copy of Base\\dsusr.dll;
  4. reads the last Windows 'Application Error' entries.

Writes INIT_CHECK.txt beside it -- send that file back.
"""
from __future__ import print_function
import os, sys, re, struct, subprocess, tempfile, time, glob, shutil, threading, platform

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDERS = [os.path.join(HERE, "Base"), os.path.join(HERE, "Projects")]
BUSES = 150000                        # the study's psseinit(150000)
TIMEOUT_S = 150                       # one start-up; a modal box counts as hung
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
SYSTEM32 = os.path.join(SYSROOT, "System32")

# Runtime libraries a PSS/E module can take from somewhere other than PSSBIN.
RUNTIME = re.compile(r"^(vcruntime140(_1)?|msvcp140(_\w+)?|concrt140|vcomp140|ucrtbase|"
                     r"libiomp5md|libiompstubs5md|libifcoremd|libifportmd|libmmd|svml_dispmd|"
                     r"libimalloc|libirngmd|libicaf|mkl_\w+|tbb\w*|wibucm64|python3\d*)\.dll$", re.I)
# Loaded first, in this order, by test 3 (a library's own dependencies before it).
PRELOAD_ORDER = ["vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                 "concrt140.dll", "vcomp140.dll", "libmmd.dll", "svml_dispmd.dll", "libirngmd.dll",
                 "libimalloc.dll", "libifcoremd.dll", "libifportmd.dll", "libiomp5md.dll",
                 "libiompstubs5md.dll", "libicaf.dll"]

LOG = []


def P(s=""):
    print(s)
    sys.stdout.flush()
    LOG.append(s)


# ---- what a .exe / .dll is ---------------------------------------------------------
def bitness(path):
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return "not a DLL"
            f.seek(struct.unpack("<I", head[60:64])[0])
            sig = f.read(6)
        if len(sig) < 6 or sig[:4] != b"PE\0\0":
            return "not a DLL"
        m = struct.unpack("<H", sig[4:6])[0]
        return {0x14c: "32-bit", 0x8664: "64-bit", 0xaa64: "ARM64"}.get(m, "machine 0x%04x" % m)
    except Exception:
        return "unreadable"


_VER = {}


def file_version(path):
    """'14.38.33130.0' from the file's version resource, or '' (not Windows / none)."""
    if os.name != "nt" or not path:
        return ""
    key = os.path.normcase(path)
    if key in _VER:
        return _VER[key]
    v = ""
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        n = ver.GetFileVersionInfoSizeW(path, None)
        if n:
            buf = ctypes.create_string_buffer(n)
            if ver.GetFileVersionInfoW(path, 0, n, buf):
                p = ctypes.c_void_p()
                ln = wintypes.UINT()
                if ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(ln)) and p.value:
                    f = ctypes.cast(p, ctypes.POINTER(wintypes.DWORD * 13)).contents
                    v = "%d.%d.%d.%d" % (f[2] >> 16, f[2] & 0xFFFF, f[3] >> 16, f[3] & 0xFFFF)
    except Exception:
        v = ""
    _VER[key] = v
    return v


def vtuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return ()


# ---- the PSS/E install, found the way the study scripts find it ------------------
def psse_install_roots():
    bases = [r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"]
    if struct.calcsize("P") == 8:
        bases.reverse()
    out = [os.environ.get("PSSE_ROOT", "")]
    for b in bases:
        for d in sorted(glob.glob(os.path.join(b, "PSSE3*")), reverse=True):
            out += sorted(glob.glob(os.path.join(d, "3[0-9].*")), reverse=True) + [d]
    return [r for r in out if r and os.path.isdir(r)]


def find_psspy():
    prefer = "PSSPY%d%d" % sys.version_info[:2]
    for root in psse_install_roots():
        if os.path.isdir(os.path.join(root, prefer)):
            return root, os.path.join(root, prefer), os.path.join(root, "PSSBIN")
    return "", "", ""


def dlls_in(folder):
    """{lower-case name: full path} of the .dll in one folder."""
    out = {}
    try:
        for n in os.listdir(folder):
            if n.lower().endswith(".dll"):
                out[n.lower()] = os.path.join(folder, n)
    except Exception:
        pass
    return out


# ---- the modules another process has loaded ----------------------------------------
_PS = {}


def list_modules(pid):
    """Full paths of the modules loaded in process pid, in load order; None if unreadable."""
    if os.name != "nt":
        try:
            out = []
            with open("/proc/%d/maps" % pid) as fh:
                for ln in fh:
                    parts = ln.split()
                    if len(parts) >= 6 and parts[5].startswith("/") and parts[5] not in out:
                        out.append(parts[5])
            return out
        except Exception:
            return None
    try:
        import ctypes
        from ctypes import wintypes
        if not _PS:
            k32 = ctypes.WinDLL("kernel32")
            ps = ctypes.WinDLL("psapi")
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            ps.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                wintypes.DWORD]
            ps.EnumProcessModulesEx.restype = wintypes.BOOL
            ps.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                                wintypes.LPWSTR, wintypes.DWORD]
            ps.GetModuleFileNameExW.restype = wintypes.DWORD
            _PS.update(k32=k32, ps=ps, arr=(wintypes.HMODULE * 4096)(),
                       buf=ctypes.create_unicode_buffer(1024))
        k32, ps, arr, buf = _PS["k32"], _PS["ps"], _PS["arr"], _PS["buf"]
        h = k32.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION | VM_READ
        if not h:
            return None
        try:
            need = wintypes.DWORD()
            if not ps.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 0x03):
                return None
            out = []
            for i in range(min(need.value // ctypes.sizeof(wintypes.HMODULE), len(arr))):
                if arr[i] and ps.GetModuleFileNameExW(h, arr[i], buf, len(buf)):
                    out.append(buf.value)
            return out
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


# ---- one PSS/E start-up, in its own process -------------------------------------
# Each step is printed BEFORE it runs, straight to the process's own stdout, so the
# last STEP line of a crashed run is the call that crashed. It is run from a file,
# not with -c: PSS/E reads the process command line and a long -c argument is an
# "Input error" to it.
CHILD = r'''
import os, sys, faulthandler
faulthandler.enable()                  # a crash prints the Python line it happened on
def say(s):
    sys.__stdout__.write(s + "\n"); sys.__stdout__.flush()
pssbin, psspy_dir, mode = os.environ["CHK_PSSBIN"], os.environ["CHK_PSSPY"], os.environ["CHK_MODE"]
for d in (pssbin, psspy_dir):          # what the study scripts do
    sys.path.insert(0, d)
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(d)
    except Exception:
        pass
if mode == "dlldir" and os.name == "nt":
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    k32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    k32.SetDllDirectoryW.restype = wintypes.BOOL
    say("STEP  SetDllDirectoryW(PSSBIN) -> %s" % bool(k32.SetDllDirectoryW(pssbin)))
if mode == "preload" and os.name == "nt":
    import ctypes
    for p in [x for x in os.environ.get("CHK_PRELOAD", "").split(os.pathsep) if x]:
        try:
            ctypes.WinDLL(p)
            say("STEP  loaded first: %s" % p)
        except Exception as e:
            say("STEP  could not load %s first: %s" % (p, e))
say("STEP  import psse35")
try:
    import psse35
except Exception as e:
    say("      (psse35 not imported: %s)" % e)
say("STEP  import psspy")
import psspy
say("STEP  psspy.psseinit(%s)" % os.environ["CHK_BUSES"])
ierr = psspy.psseinit(int(os.environ["CHK_BUSES"]))
say("STEP  psseinit returned %r" % (ierr,))
try:
    say("      psseversion %r" % (psspy.psseversion(),))
except Exception as e:
    say("      psseversion raised %s" % e)
say("RESULT OK")
'''

NTSTATUS = {0xC0000005: "ACCESS VIOLATION",
            0xC0000409: "STACK BUFFER OVERRUN / fast-fail",
            0xC000007B: "INVALID IMAGE FORMAT (a 32-bit DLL in a 64-bit process, or the reverse)",
            0xC0000135: "DLL NOT FOUND",
            0xC0000139: "ENTRY POINT NOT FOUND (a DLL of the wrong version)",
            0xC0000142: "DLL INITIALISATION FAILED",
            0xC0000374: "HEAP CORRUPTION",
            0xC000001D: "ILLEGAL INSTRUCTION",
            0xC00000FD: "STACK OVERFLOW",
            0xC0000417: "INVALID C RUNTIME PARAMETER"}


def rc_text(rc):
    if rc is None:
        return "HUNG -- killed after %d s (a PSS/E box waiting for a click?)" % TIMEOUT_S
    u = rc & 0xFFFFFFFF
    if u in NTSTATUS:
        return "%d = 0x%08X %s" % (rc, u, NTSTATUS[u])
    return "%d" % rc


_FH = re.compile(r"^(Windows fatal exception: (.+)|Fatal Python error: (.+)|(Current thread|Thread) 0x"
                 r"|\s+File \"|Stack \(most recent)")


class Ctx(object):
    """What the tests need to know about this machine."""
    root = psspy_dir = pssbin = tmp = child = ""
    pssbin_dlls = {}
    base_mods = None              # test 1's modules, to print the others as differences


def own_dirs(ctx):
    """Folders a module may come from without being worth a line: PSS/E, Python, Windows."""
    out = [ctx.root, sys.base_prefix, SYSROOT]
    return [os.path.normcase(os.path.abspath(d)) for d in out if d]


def describe(path, ctx):
    v = file_version(path)
    n = os.path.basename(path).lower()
    extra = ""
    pb = ctx.pssbin_dlls.get(n)
    if pb and os.path.normcase(pb) != os.path.normcase(path):
        pv = file_version(pb)
        extra = "   <-- PSSBIN has its own copy%s" % ((", " + pv) if pv else "")
        if v and pv and vtuple(v) < vtuple(pv):
            extra += " (NEWER)"
    return "%-16s %s%s" % (v or "-", path, extra)


def report_modules(mods, ctx):
    if not mods:
        P("   | (could not read which DLLs this process had loaded)")
        return
    names = set(os.path.basename(m).lower() for m in mods)
    P("   | %d modules loaded when it %s; GIC.dll %s, MUSTENG.dll %s"
      % (len(mods), "was last seen", "in" if "gic.dll" in names else "NOT in",
         "in" if "musteng.dll" in names else "NOT in"))
    runtime = [m for m in mods if RUNTIME.match(os.path.basename(m))]
    mine = own_dirs(ctx)
    foreign = [m for m in mods if not any(os.path.normcase(os.path.abspath(m)).startswith(d + os.sep)
                                          for d in mine)]
    if ctx.base_mods is None:
        P("   | runtime libraries it used:")
        for m in runtime:
            P("   |    %s" % describe(m, ctx))
        P("   | loaded from OUTSIDE PSS/E, Python and Windows:%s" % ("" if foreign else " none"))
        for m in foreign:
            P("   |    %s" % describe(m, ctx))
        ctx.base_mods = mods
        return
    base = set(os.path.normcase(m) for m in ctx.base_mods)
    now = set(os.path.normcase(m) for m in mods)
    diff = [m for m in runtime + foreign if os.path.normcase(m) not in base]
    gone = [m for m in ctx.base_mods if RUNTIME.match(os.path.basename(m))
            and os.path.normcase(m) not in now]
    if not diff and not gone:
        P("   | runtime libraries: the same as test 1")
    for m in diff:
        P("   | + %s" % describe(m, ctx))
    for m in gone:
        P("   | - %s" % describe(m, ctx))


def start_psse(label, cwd, ctx, mode="study", exe=None, extra_env=None):
    P("")
    P("-- %s" % label)
    env = dict(os.environ)
    env.update({"CHK_PSSPY": ctx.psspy_dir, "CHK_PSSBIN": ctx.pssbin, "CHK_MODE": mode,
                "CHK_BUSES": str(BUSES)})
    env.update(extra_env or {})
    t0 = time.time()
    try:
        p = subprocess.Popen([exe or sys.executable, "-u", ctx.child], cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:
        P("   could not start Python: %s" % e)
        return None
    chunks = []
    reader = threading.Thread(target=lambda: chunks.append(p.stdout.read()))
    reader.daemon = True
    reader.start()
    mods = None
    rc = None
    while True:
        m = list_modules(p.pid)
        if m:
            mods = m                      # the last list read before it ended
        rc = p.poll()
        if rc is not None:
            break
        if time.time() - t0 > TIMEOUT_S:
            p.kill()
            p.wait()
            rc = None
            break
        time.sleep(0.1)
    reader.join(10)
    out = b"".join(c for c in chunks if c)
    text = out.decode("mbcs" if os.name == "nt" else "utf-8", "replace")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    faults = {}
    shown = []
    for ln in lines:
        mm = _FH.match(ln)
        if mm:
            kind = (mm.group(2) or mm.group(3) or "").strip()
            if kind:
                faults[kind] = faults.get(kind, 0) + 1
            continue
        if re.match(r"^\s*(PSS\(R\)E Version|Copyright|Siemens|Power Tech|This program|licensed in|"
                    r"All use|by  PTI|laws  of|treaties)", ln):
            continue                                  # the banner, every time
        shown.append(ln)
    for ln in shown[-20:]:
        P("   | %s" % ln)
    if faults:
        P("   | (Python's fault handler saw: %s)"
          % ", ".join("%d x %s" % (n, k) for k, n in sorted(faults.items())))
    report_modules(mods, ctx)
    steps = [ln for ln in lines if ln.startswith("STEP")]
    ok = rc == 0 and any(ln.startswith("RESULT OK") for ln in lines)
    P("   exit: %s   (%.0f s)" % (rc_text(rc), time.time() - t0))
    P("   => %s" % ("PSS/E STARTED" if ok else
                     "FAILED " + ("at: " + steps[-1][4:].strip() if steps else "before the first step")))
    return ok


# ---- Windows' own record of the crash -----------------------------------------
def event_log(n):
    P("")
    P("== Windows event log: the last %d 'Application Error' entries (newest first) ==" % n)
    if os.name != "nt":
        P("   (not Windows)")
        return
    q = "*[System[Provider[@Name='Application Error'] and (EventID=1000)]]"
    try:
        out = subprocess.check_output(["wevtutil", "qe", "Application", "/q:" + q,
                                       "/c:%d" % n, "/rd:true", "/f:text"],
                                      stderr=subprocess.STDOUT)
        text = out.decode("mbcs", "replace")
    except Exception as e:
        P("   wevtutil failed: %s" % e)
        return
    ev = {}
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("Event["):
            if ev:
                rows.append(ev)
            ev = {}
        for key, pat in (("date", r"^Date:\s*(\S+)"), ("app", r"^Faulting application name:\s*([^,]+)"),
                         ("mod", r"^Faulting module name:\s*([^,]+)"),
                         ("path", r"^Faulting module path:\s*(.+)"),
                         ("code", r"^Exception code:\s*(\S+)"), ("off", r"^Fault offset:\s*(\S+)")):
            m = re.match(pat, s, re.I)
            if m:
                ev[key] = m.group(1).strip()
    if ev:
        rows.append(ev)
    for e in rows:
        P("   %-23s %-11s %-18s %-11s %s   %s" % (e.get("date", "?")[:23], e.get("app", "?"),
                                                  e.get("mod", "?"), e.get("code", "?"),
                                                  e.get("off", ""), e.get("path", "")))
    if not rows:
        P("   (no entries)")


def main():
    P("INIT CHECK v3 -- %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    P("Python   : %s" % sys.version.replace("\n", " "))
    P("           %d-bit, %s" % (struct.calcsize("P") * 8, sys.executable))
    P("Windows  : %s" % platform.platform())
    P("CPU      : %s, %s logical" % (os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor(),
                                     os.cpu_count()))
    P("study    : %s" % HERE)
    ctx = Ctx()
    ctx.root, ctx.psspy_dir, ctx.pssbin = find_psspy()
    P("PSS/E    : %s" % (ctx.psspy_dir or "*** no PSSPY%d%d folder found ***" % sys.version_info[:2]))
    envs = sorted(k for k in os.environ if re.match(
        r"^(KMP_|OMP_|MKL_|INTEL|IFORT|ONEAPI|I_MPI|PSSE|PTI|CODEMETER|WIBU|PYTHON|TBB|CONDA|"
        r"__COMPAT_LAYER)", k, re.I))
    P("environment that can change how PSS/E loads: %s"
      % (", ".join("%s=%s" % (k, os.environ[k][:60]) for k in envs) if envs else "none set"))
    pti = [d for d in os.environ.get("PATH", "").split(os.pathsep) if re.search(r"PTI|PSSE", d, re.I)]
    P("PATH entries naming PTI/PSSE: %s" % (", ".join(pti) if pti else "none"))
    me = "64-bit" if struct.calcsize("P") == 8 else "32-bit"

    P("")
    P("== DLLs in the case folders (this PSS/E can load only %s ones) ==" % me)
    for d in FOLDERS:
        if not os.path.isdir(d):
            P("   %s  (folder not found)" % d)
            continue
        kinds = {}
        for f in sorted(glob.glob(os.path.join(d, "*.dll"))):
            kinds.setdefault(bitness(f), []).append(os.path.basename(f))
        P("   %s: %s" % (d, ", ".join("%d %s" % (len(v), k) for k, v in sorted(kinds.items()))
                         or "no .dll"))

    if not ctx.psspy_dir:
        P("")
        P("== verdict ==")
        P("   PSS/E was not found for this Python.")
        return write_log()

    # ---- which PSSBIN DLLs another folder supplies first inside python.exe ----
    ctx.pssbin_dlls = dlls_in(ctx.pssbin)
    P("")
    P("== DLLs PSS/E ships that python.exe finds somewhere else first ==")
    P("   (PSS/E's GUI runs from PSSBIN and always gets PSSBIN's copies; python.exe")
    P("    looks in its own folder, then System32, before it gets to PSSBIN)")
    pydir = sys.base_prefix
    rows = []
    for where, folder in (("Python's folder", pydir), ("System32", SYSTEM32), ("Windows", SYSROOT)):
        other = dlls_in(folder)
        for n in sorted(set(other) & set(ctx.pssbin_dlls)):
            a, b = file_version(other[n]), file_version(ctx.pssbin_dlls[n])
            older = bool(a and b and vtuple(a) < vtuple(b))
            rows.append((not older, "   %-22s %-16s %-16s PSSBIN %s%s"
                         % (n, where, a or "-", b or "-", "   <-- OLDER than PSS/E's" if older else "")))
    rows.sort(key=lambda x: x[0])                    # the older copies first
    for _k, ln in rows[:40]:
        P(ln)
    if len(rows) > 40:
        P("   ... (%d more, none of them older than PSS/E's)" % (len(rows) - 40)
          if all(k for k, _l in rows[40:]) else "   ... (%d more)" % (len(rows) - 40))
    if not rows:
        P("   none")
    P("   PSS/E's own runtime libraries in PSSBIN:")
    for n in sorted(ctx.pssbin_dlls):
        if RUNTIME.match(n):
            P("      %-22s %s" % (n, file_version(ctx.pssbin_dlls[n]) or "-"))

    # ---- the tests ----
    ctx.tmp = tempfile.mkdtemp(prefix="z7_init_check_")
    ctx.child = os.path.join(ctx.tmp, "init_child.py")
    with open(ctx.child, "w") as fh:
        fh.write(CHILD)
    empty = os.path.join(ctx.tmp, "empty")
    os.makedirs(empty)
    P("")
    P("== starting PSS/E in an EMPTY folder, one process per test ==")
    results = []
    results.append(("study", start_psse("1. as the study starts it", empty, ctx), {}))
    results.append(("dlldir", start_psse("2. PSSBIN first in the DLL search (SetDllDirectory), as for "
                                         "the PSS/E GUI", empty, ctx, mode="dlldir"),
                    {"mode": "dlldir"}))
    pre = []
    sys32 = dlls_in(SYSTEM32)
    pyown = dlls_in(pydir)
    for n in PRELOAD_ORDER:
        if n in ctx.pssbin_dlls:
            pre.append(ctx.pssbin_dlls[n])
        elif n == "vcruntime140_1.dll" and n in sys32 and n in pyown and \
                vtuple(file_version(sys32[n])) > vtuple(file_version(pyown[n])):
            pre.append(sys32[n])
    if pre:
        results.append(("preload", start_psse("3. PSS/E's own runtime libraries loaded first: %s"
                                              % ", ".join(os.path.basename(x) for x in pre),
                                              empty, ctx, mode="preload",
                                              extra_env={"CHK_PRELOAD": os.pathsep.join(pre)}),
                        {"mode": "preload", "extra_env": {"CHK_PRELOAD": os.pathsep.join(pre)}}))
    else:
        P("")
        P("-- 3. skipped: PSSBIN has none of the runtime libraries a test could load first")
    # 4. a python.exe with the newest Visual C++ runtime beside it
    best = {}
    for n in ("vcruntime140.dll", "vcruntime140_1.dll"):
        cands = [p for p in (ctx.pssbin_dlls.get(n), sys32.get(n)) if p]
        cands.sort(key=lambda p: vtuple(file_version(p)), reverse=True)
        if cands:
            best[n] = cands[0]
    own_v = file_version(pyown.get("vcruntime140.dll", ""))
    new_v = file_version(best.get("vcruntime140.dll", ""))
    if os.environ.get("CHK_FORCE_PYCOPY") or (own_v and new_v and vtuple(new_v) > vtuple(own_v)):
        pyexe = os.path.join(ctx.tmp, "python_copy")
        os.makedirs(pyexe)
        try:
            shutil.copy2(sys.executable, pyexe)
            for f in glob.glob(os.path.join(pydir, "python3*.dll")):
                shutil.copy2(f, pyexe)
            for n, src in best.items():
                shutil.copy2(src, os.path.join(pyexe, n))
            exe = os.path.join(pyexe, os.path.basename(sys.executable))
            kw = {"exe": exe, "extra_env": {"PYTHONHOME": sys.base_prefix}}
            results.append(("vcrt", start_psse("4. a copy of python.exe with the Visual C++ runtime %s "
                                               "beside it (Python's own is %s)"
                                               % (new_v or "?", own_v or "?"), empty, ctx, **kw), kw))
        except Exception as e:
            P("")
            P("-- 4. could not set up the python.exe copy: %s" % e)
    else:
        P("")
        P("-- 4. skipped: no Visual C++ runtime newer than Python's own (%s)" % (own_v or "?"))
    dsusr = os.path.join(FOLDERS[0], "dsusr.dll")
    good = [(k, kw) for k, ok, kw in results if ok]
    if good and os.path.isfile(dsusr):
        only = os.path.join(ctx.tmp, "dsusr_only")
        os.makedirs(only)
        shutil.copy2(dsusr, only)
        k, kw = good[0]
        results.append(("dsusr", start_psse("5. test '%s' again, beside a copy of Base\\dsusr.dll (%s)"
                                            % (k, bitness(dsusr)), only, ctx, **kw), kw))
    shutil.rmtree(ctx.tmp, ignore_errors=True)
    if not all(ok for _k, ok, _kw in results) and os.name == "nt":
        time.sleep(5)                     # Windows writes the crash record a moment later
    event_log(8)

    P("")
    P("== verdict ==")
    r = dict((k, ok) for k, ok, _kw in results)
    if r.get("study"):
        P("   PSS/E starts the way the study starts it -- the crash is later than start-up.")
    elif r.get("dlldir"):
        P("   DLL SEARCH ORDER: PSS/E starts with PSSBIN first in the DLL search. A DLL in")
        P("   System32 (listed above) was being used in place of PSS/E's own copy.")
        P("   -> the study scripts can set PSSBIN first before they load PSS/E.")
    elif r.get("preload"):
        P("   RUNTIME LIBRARIES: PSS/E starts when its own runtime libraries are loaded first.")
        P("   -> the study scripts can load them first before they load PSS/E.")
    elif r.get("vcrt"):
        P("   VISUAL C++ RUNTIME: PSS/E starts from a python.exe that has the newer Visual C++")
        P("   runtime beside it. Python's own vcruntime140.dll is older than PSS/E needs.")
        P("   -> copy %s and %s into %s"
          % (best.get("vcruntime140.dll", "?"), best.get("vcruntime140_1.dll", "?"), pydir))
    else:
        P("   PSS/E does not start in any of these ways. The DLLs it uses are listed above.")
        P("   Next: does the PSS/E 35.6 GUI start, and can it open the .sav? If the GUI fails")
        P("   too, it is the install or the licence (repair PSS/E 35.6 / the CodeMeter runtime).")
    if r.get("dsusr") is False:
        P("   ALSO: it dies beside Base\\dsusr.dll -- that file must go (the study scripts now")
        P("   move a wrong-bitness dsusr.dll aside by themselves).")
    write_log()


def write_log():
    out = os.path.join(HERE, "INIT_CHECK.txt")
    try:
        import io
        with io.open(out, "w", encoding="utf-8", errors="replace") as f:
            f.write(u"\n".join(LOG) + u"\n")
        print("\nwritten: %s -- send this file" % out)
    except Exception as e:
        print("\ncould not write %s: %s" % (out, e))


if __name__ == "__main__":
    main()
