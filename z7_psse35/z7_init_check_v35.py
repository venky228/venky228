# -*- coding: utf-8 -*-
"""z7_init_check_v35.py -- why does PSS/E 35 stop at start-up?

Run it with the same Python as z7_main_v35.py, from the study root:

    python.exe z7_init_check_v35.py

It changes nothing. It
  1. lists every .dll in Base\\ and Projects\\ with its bitness. PSS/E 35 is
     64-bit: it cannot load a 32-bit (PSS/E 34) dsusr.dll or vendor model DLL,
     and it loads dsusr.dll from the working folder inside psseinit();
  2. starts PSS/E in a separate process several ways -- an empty folder, an
     empty folder holding only a copy of Base\\dsusr.dll, Base\\, Projects\\,
     and Base\\ with the output redirected the way the study does it -- so a
     crash in one does not hide the others;
  3. reads the last Windows 'Application Error' entries, which name the DLL
     that crashed.

Writes INIT_CHECK.txt beside it -- send that file back.
"""
from __future__ import print_function
import os, sys, re, struct, subprocess, tempfile, time, glob, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDERS = [os.path.join(HERE, "Base"), os.path.join(HERE, "Projects")]
BUS_SIZE = 150000                     # the same psseinit(150000) the study makes
TIMEOUT_S = 240                       # one start-up; a modal box counts as hung

LOG = []


def P(s=""):
    print(s)
    sys.stdout.flush()
    LOG.append(s)


# ---- bitness of a .dll -------------------------------------------------------
def pe_machine(path):
    """'32-bit', '64-bit', or why it could not be told."""
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
    except Exception as e:
        return "unreadable (%s)" % e


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
import os, sys, faulthandler
faulthandler.enable()                  # a crash prints the Python line it happened on
def say(s):
    sys.__stdout__.write(s + "\n"); sys.__stdout__.flush()
for d in (os.environ["CHK_PSSBIN"], os.environ["CHK_PSSPY"]):
    sys.path.insert(0, d)
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(d)
    except Exception:
        pass
say("STEP  import psse35")
try:
    import psse35
except Exception as e:
    say("      (psse35 not imported: %s)" % e)
say("STEP  import psspy")
import psspy
say("STEP  import dyntools")
import dyntools
if os.environ.get("CHK_MODE") == "study":
    class _Tee(object):
        def __init__(self, fh, con): self.fh, self.con = fh, con
        def write(self, d):
            for s in (self.fh, self.con):
                try: s.write(d); s.flush()
                except Exception: pass
        def flush(self):
            for s in (self.fh, self.con):
                try: s.flush()
                except Exception: pass
        def isatty(self): return False
    _fh = open(os.path.join(os.environ["CHK_TMP"], "tee_%d.log" % os.getpid()), "w")
    sys.stdout = _Tee(_fh, sys.stdout); sys.stderr = _Tee(_fh, sys.stderr)
    say("STEP  redirect.psse2py()  (output through a tee, as the study does)")
    import redirect
    redirect.psse2py()
    say("STEP  psspy.psseversion() before psseinit  (the study's 'is a GUI open' test)")
    try:
        say("      -> %r" % (psspy.psseversion(),))
    except Exception as e:
        say("      -> raised %s" % e)
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


def start_psse(label, cwd, mode, psspy_dir, pssbin, tmp):
    P("")
    P("-- %s" % label)
    P("   folder: %s" % cwd)
    env = dict(os.environ)
    env.update({"CHK_PSSPY": psspy_dir, "CHK_PSSBIN": pssbin, "CHK_MODE": mode,
                "CHK_BUSES": str(BUS_SIZE), "CHK_TMP": tmp})
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
    for ln in lines[-40:]:
        P("   | %s" % ln)
    steps = [ln for ln in lines if ln.startswith("STEP")]
    ok = rc == 0 and any(ln.startswith("RESULT OK") for ln in lines)
    P("   exit: %s   (%.0f s)" % (rc_text(rc), time.time() - t0))
    if ok:
        P("   => PSS/E STARTED")
    else:
        P("   => FAILED %s" % ("at: " + steps[-1][4:].strip() if steps else "before the first step"))
    return ok


# ---- Windows' own record of the crash -----------------------------------------
def event_log():
    P("")
    P("== Windows event log: the last 'Application Error' entries ==")
    if os.name != "nt":
        P("   (not Windows)")
        return
    q = "*[System[Provider[@Name='Application Error'] and (EventID=1000)]]"
    try:
        out = subprocess.check_output(["wevtutil", "qe", "Application", "/q:" + q,
                                       "/c:6", "/rd:true", "/f:text"],
                                      stderr=subprocess.STDOUT)
        text = out.decode("mbcs", "replace")
    except Exception as e:
        P("   wevtutil failed: %s" % e)
        return
    keep = re.compile(r"^\s*(Event\[|Date:|Faulting (application|module) (name|path)|"
                      r"Exception code|Fault offset)", re.I)
    shown = [ln.rstrip() for ln in text.splitlines() if keep.search(ln)]
    if not shown:
        shown = [ln.rstrip() for ln in text.splitlines() if ln.strip()][:60]
    for ln in shown or ["   (no entries)"]:
        P("   %s" % ln)


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
    me = "64-bit" if struct.calcsize("P") == 8 else "32-bit"

    P("")
    P("== DLLs in the case folders (PSS/E %s can load only %s ones) ==" % ("35" if me == "64-bit" else "34", me))
    bad = []
    for d in FOLDERS:
        if not os.path.isdir(d):
            P("%s  (folder not found)" % d)
            continue
        dlls = sorted(glob.glob(os.path.join(d, "*.dll")))
        P("%s  (%d .dll)" % (d, len(dlls)))
        for f in dlls:
            kind = pe_machine(f)
            wrong = kind in ("32-bit", "64-bit") and kind != me
            if wrong:
                bad.append(f)
            P("   %-8s %10d  %s  %s%s" % (kind, os.path.getsize(f),
                                         time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f))),
                                         os.path.basename(f), "   <-- WRONG BITNESS" if wrong else ""))
    P("")
    P("== compile / link .bat files ==")
    for d in FOLDERS:
        for f in sorted(glob.glob(os.path.join(d, "*.bat"))):
            try:
                with open(f, "rb") as fh:
                    body = fh.read().decode("latin-1")
            except Exception as e:
                P("   %s: unreadable (%s)" % (f, e))
                continue
            refs = sorted(set(m.group(0) for m in
                              re.finditer(r"PSSE3[0-9][^\s\"';]*", body, re.I)))
            P("   %s\\%s  -> %s" % (os.path.basename(d), os.path.basename(f),
                                   ", ".join(refs) if refs else "(names no PSSE3x path)"))

    results = {}
    if psspy_dir:
        tmp = tempfile.mkdtemp(prefix="z7_init_check_")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        P("")
        P("== starting PSS/E, one process per test ==")
        P("   (if Windows shows 'python.exe has stopped working', click Close -- the")
        P("    crash is then recorded in the event log read at the end)")
        results["empty"] = start_psse("1. an EMPTY folder, plain output", empty, "plain",
                                      psspy_dir, pssbin, tmp)
        dsusr = os.path.join(FOLDERS[0], "dsusr.dll")
        if os.path.isfile(dsusr):
            only = os.path.join(tmp, "dsusr_only")
            os.makedirs(only)
            shutil.copy2(dsusr, only)
            results["dsusr"] = start_psse("%d. an empty folder holding ONLY a copy of Base\\dsusr.dll "
                                          "(%s) -- PSS/E loads it at psseinit"
                                          % (len(results) + 1, pe_machine(dsusr)),
                                          only, "plain", psspy_dir, pssbin, tmp)
        for d in FOLDERS:
            if os.path.isdir(d):
                results[d] = start_psse("%d. %s, plain output" % (len(results) + 1, os.path.basename(d)),
                                        d, "plain", psspy_dir, pssbin, tmp)
        if os.path.isdir(FOLDERS[0]):
            results["study"] = start_psse("%d. Base, output through redirect.psse2py() and a tee "
                                          "(exactly as the study starts)" % (len(results) + 1),
                                          FOLDERS[0], "study", psspy_dir, pssbin, tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        if not all(results.values()) and os.name == "nt":
            time.sleep(5)                 # Windows writes the crash record a moment later
    event_log()

    P("")
    P("== verdict ==")
    if results:
        e = results.get("empty")
        rest = [k for k in results if k != "empty"]
        names = {"dsusr": "the folder holding only dsusr.dll", "study": "Base as the study starts it"}
        if e and all(results[k] for k in rest):
            P("   PSS/E starts in every test -- the crash is later than start-up.")
        elif e and results.get("dsusr") is False:
            P("   PSS/E starts in an empty folder and dies with Base\\dsusr.dll beside it:")
            P("   dsusr.dll is the cause.")
        elif e and not all(results[k] for k in rest):
            P("   PSS/E starts in an empty folder but NOT in: %s"
              % ", ".join(names.get(k, os.path.basename(k)) for k in rest if not results[k]))
            P("   -> something in that folder (or the output redirect) is what kills it.")
        elif e is False:
            P("   PSS/E does not start even in an empty folder -- the install, the licence or")
            P("   this Python, not the study. The event log above names the DLL.")
    if bad:
        P("   WRONG-BITNESS DLL(s) -- PSS/E %s cannot load these:" % ("35" if me == "64-bit" else "34"))
        for f in bad:
            P("      %s" % f)
        if any(os.path.basename(f).lower() == "dsusr.dll" for f in bad):
            P("   dsusr.dll: delete it; the build compiles a new one from conec/conet, once")
            P("   MyCompile35.bat and MyCload41.bat point at PSS/E 35.")
        if any(os.path.basename(f).lower() != "dsusr.dll" for f in bad):
            P("   the others: get their PSS/E 35 (64-bit) builds from the vendor, or move them")
            P("   out of the case folder if the deck does not use them.")
    elif not results:
        P("   no DLL of the wrong bitness, and PSS/E was not found to start.")
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
