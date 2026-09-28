# -*- coding: utf-8 -*-
"""z7_init_check_v35.py -- why does PSS/E 35 stop at start-up?

Run it with the same Python as z7_main_v35.py, from the study root:

    python.exe z7_init_check_v35.py

It changes nothing. It
  1. counts the .dll in Base\\ and Projects\\ by bitness. PSS/E 35 is 64-bit:
     it cannot load a 32-bit (PSS/E 34) dsusr.dll or vendor model DLL;
  2. reads how much stack python.exe and psse35.exe reserve for their main thread;
  3. starts PSS/E in an empty folder, one separate process per test, in the ways
     that tell the causes apart: the study's psseinit(150000), PSS/E's default
     size, 50000 buses, psseinit(150000) on a thread with a 255 MB stack, and
     psse35.py setting up the paths on its own. The first way that starts is
     tried again beside a copy of Base\\dsusr.dll;
  4. reads the last Windows 'Application Error' entries, which name the DLL
     that crashed.

Writes INIT_CHECK.txt beside it -- send that file back.
"""
from __future__ import print_function
import os, sys, re, struct, subprocess, tempfile, time, glob, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDERS = [os.path.join(HERE, "Base"), os.path.join(HERE, "Projects")]
BIG_STACK_MB = 255                    # threading.stack_size() takes < 256 MB on Windows
TIMEOUT_S = 240                       # one start-up; a modal box counts as hung

LOG = []


def P(s=""):
    print(s)
    sys.stdout.flush()
    LOG.append(s)


# ---- what a .exe / .dll is ---------------------------------------------------------
def pe_header(path):
    """(machine, stack reserve in bytes) of a Windows executable, or (None, None)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return None, None
            f.seek(struct.unpack("<I", head[60:64])[0])
            hdr = f.read(24 + 80)
        if len(hdr) < 24 + 80 or hdr[:4] != b"PE\0\0":
            return None, None
        machine = struct.unpack("<H", hdr[4:6])[0]
        opt = hdr[24:]
        magic = struct.unpack("<H", opt[:2])[0]
        if magic == 0x20b:                                  # PE32+ (64-bit)
            stack = struct.unpack("<Q", opt[72:80])[0]
        elif magic == 0x10b:                                # PE32 (32-bit)
            stack = struct.unpack("<I", opt[72:76])[0]
        else:
            stack = None
        return machine, stack
    except Exception:
        return None, None


def bitness(path):
    m = pe_header(path)[0]
    if m is None:
        return "not a DLL"
    return {0x14c: "32-bit", 0x8664: "64-bit", 0xaa64: "ARM64"}.get(m, "machine 0x%04x" % m)


def mb(n):
    return "?" if n is None else "%.1f MB" % (n / 1048576.0)


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
            return os.path.join(root, prefer), os.path.join(root, "PSSBIN")
    return "", ""


# ---- one PSS/E start-up, in its own process -------------------------------------
# Each step is printed BEFORE it runs, straight to the process's own stdout, so the
# last STEP line of a crashed run is the call that crashed.
CHILD = r'''
import os, sys, faulthandler, threading
faulthandler.enable()                  # a crash prints the Python line it happened on
def say(s):
    sys.__stdout__.write(s + "\n"); sys.__stdout__.flush()
if os.environ["CHK_SETUP"] == "study":
    # what the study scripts do: both folders on sys.path, PATH and the DLL search
    for d in (os.environ["CHK_PSSBIN"], os.environ["CHK_PSSPY"]):
        sys.path.insert(0, d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
else:
    # psse35.py alone sets up PSSBIN, as Siemens' own examples do
    sys.path.insert(0, os.environ["CHK_PSSPY"])
say("STEP  import psse35")
try:
    import psse35
except Exception as e:
    say("      (psse35 not imported: %s)" % e)
say("STEP  import psspy")
import psspy
buses = os.environ["CHK_BUSES"]
stack = int(os.environ["CHK_STACK_MB"])
def start():
    on = "  on a %d MB-stack thread" % stack if stack else ""
    if buses:
        say("STEP  psspy.psseinit(%s)%s" % (buses, on))
        ierr = psspy.psseinit(int(buses))
    else:
        say("STEP  psspy.psseinit()  (PSS/E's default size)%s" % on)
        ierr = psspy.psseinit()
    say("STEP  psseinit returned %r" % (ierr,))
    try:
        say("      psseversion %r" % (psspy.psseversion(),))
    except Exception as e:
        say("      psseversion raised %s" % e)
    say("RESULT OK")
if stack:
    threading.stack_size(stack * 1024 * 1024)
    t = threading.Thread(target=start)
    t.start(); t.join()
else:
    start()
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


def start_psse(label, cwd, psspy_dir, pssbin, buses="150000", stack_mb=0, setup="study"):
    P("")
    P("-- %s" % label)
    env = dict(os.environ)
    env.update({"CHK_PSSPY": psspy_dir, "CHK_PSSBIN": pssbin, "CHK_BUSES": buses,
                "CHK_STACK_MB": str(stack_mb), "CHK_SETUP": setup})
    t0 = time.time()
    try:
        p = subprocess.Popen([sys.executable, "-u", "-c", CHILD], cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:
        P("   could not start Python: %s" % e)
        return None
    try:
        out, _ = p.communicate(timeout=TIMEOUT_S)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        rc = None
    text = out.decode("mbcs" if os.name == "nt" else "utf-8", "replace") if out else ""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    # faulthandler prints every exception PSS/E raises on the way down -- count them
    faults = {}
    shown = []
    for ln in lines:
        m = _FH.match(ln)
        if m:
            kind = (m.group(2) or m.group(3) or "").strip()
            if kind:
                faults[kind] = faults.get(kind, 0) + 1
            continue
        shown.append(ln)
    for ln in shown[-25:]:
        P("   | %s" % ln)
    if faults:
        P("   | (Python's fault handler saw: %s)"
          % ", ".join("%d x %s" % (n, k) for k, n in sorted(faults.items())))
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
    P("INIT CHECK -- %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    P("Python   : %s" % sys.version.replace("\n", " "))
    P("           %d-bit, %s" % (struct.calcsize("P") * 8, sys.executable))
    P("study    : %s" % HERE)
    psspy_dir, pssbin = find_psspy()
    P("PSS/E    : %s" % (psspy_dir or "*** no PSSPY%d%d folder found ***" % sys.version_info[:2]))
    if os.environ.get("PSSE_ROOT"):
        P("PSSE_ROOT: %s" % os.environ["PSSE_ROOT"])
    pti = [d for d in os.environ.get("PATH", "").split(os.pathsep) if re.search(r"PTI|PSSE", d, re.I)]
    P("PATH entries naming PTI/PSSE: %s" % (", ".join(pti) if pti else "none"))
    P("")
    P("== main-thread stack each program reserves ==")
    exes = [sys.executable] + (sorted(glob.glob(os.path.join(pssbin, "psse3*.exe")))[:3] if pssbin else [])
    for exe in exes:
        P("   %-9s %s" % (mb(pe_header(exe)[1]), exe))
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
        for k, v in sorted(kinds.items()):
            if k != me:
                P("      %s: %s%s" % (k, ", ".join(v[:6]),
                                     " ... (%d more)" % (len(v) - 6) if len(v) > 6 else ""))

    results = []
    if psspy_dir:
        tmp = tempfile.mkdtemp(prefix="z7_init_check_")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        P("")
        P("== starting PSS/E in an EMPTY folder, one process per test ==")
        tests = [("study", "1. as the study starts it: psseinit(150000), main thread",
                  dict(buses="150000")),
                 ("default", "2. PSS/E's default size: psseinit(), main thread",
                  dict(buses="")),
                 ("50000", "3. psseinit(50000), main thread",
                  dict(buses="50000")),
                 ("stack", "4. psseinit(150000) on a thread with a %d MB stack" % BIG_STACK_MB,
                  dict(buses="150000", stack_mb=BIG_STACK_MB)),
                 ("psse35", "5. psse35.py sets up the paths on its own, psseinit(150000), main thread",
                  dict(buses="150000", setup="psse35"))]
        for key, label, kw in tests:
            results.append((key, start_psse(label, empty, psspy_dir, pssbin, **kw), kw))
        dsusr = os.path.join(FOLDERS[0], "dsusr.dll")
        good = [(k, kw) for k, ok, kw in results if ok]
        if good and os.path.isfile(dsusr):
            only = os.path.join(tmp, "dsusr_only")
            os.makedirs(only)
            shutil.copy2(dsusr, only)
            k, kw = good[0]
            results.append(("dsusr", start_psse("6. test '%s' again, beside a copy of Base\\dsusr.dll (%s)"
                                                % (k, bitness(dsusr)), only, psspy_dir, pssbin, **kw), kw))
        shutil.rmtree(tmp, ignore_errors=True)
        if not all(ok for _k, ok, _kw in results) and os.name == "nt":
            time.sleep(5)                 # Windows writes the crash record a moment later
    event_log(12)

    P("")
    P("== verdict ==")
    r = dict((k, ok) for k, ok, _kw in results)
    if not results:
        P("   PSS/E was not found for this Python.")
    elif r.get("study"):
        P("   PSS/E starts the way the study starts it -- the crash is later than start-up.")
    elif r.get("stack"):
        P("   STACK: PSS/E starts on a %d MB-stack thread and not on python.exe's main thread."
          % BIG_STACK_MB)
        P("   -> the study scripts must call PSS/E from a thread with a large stack.")
    elif r.get("default") or r.get("50000"):
        P("   BUS SIZE: psseinit(150000) dies, a smaller size starts (default: %s, 50000: %s)."
          % ("starts" if r.get("default") else "dies", "starts" if r.get("50000") else "dies"))
        P("   -> the study scripts must start PSS/E with the smaller size.")
    elif r.get("psse35"):
        P("   PATHS: PSS/E starts when psse35.py sets up its own paths, and not with the")
        P("   study's PATH / add_dll_directory set-up. -> the start-up code must change.")
    else:
        P("   PSS/E does not start in ANY of these ways, in an empty folder. That is the")
        P("   install, the licence or this Python -- not the study. Check that the PSS/E 35")
        P("   GUI opens and runs, and try Python 3.9 (PSSPY39) with this same check.")
    if r.get("dsusr") is False:
        P("   ALSO: it dies beside Base\\dsusr.dll -- that file must go (the study scripts now")
        P("   move a wrong-bitness dsusr.dll aside by themselves).")
    wrong = 0
    for d in FOLDERS:
        wrong += sum(1 for f in glob.glob(os.path.join(d, "*.dll")) if bitness(f) not in (me, "not a DLL"))
    if wrong:
        P("   %d .dll in Base\\ and Projects\\ are not %s. PSS/E 35 cannot use them: each model"
          % (wrong, me))
        P("   the deck uses needs its PSS/E 35 (64-bit) build from the vendor.")
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
