# -*- coding: utf-8 -*-
"""
z4_mark_done_con.py -- give a finished run the .done marker it never got.

WHY THIS EXISTS
  A scenario is treated as finished only when a .done marker sits beside its
  .out. The marker is written by the run that produced the file, or by a plot
  pass that read it afterwards -- and a plot pass reads at minutes per file,
  so a folder of 290 results can sit for days with its .out files complete and
  most of them unmarked. Everything downstream then misreads the folder:

      PIPELINE = "all"   re-SIMULATES every unmarked fault (hours per fault)
      the report phase   refuses to score them -- no marker, no verdict
      the comparison     shows them as CRASHED, or leaves the base column empty

  This script decides the question the cheap way -- from the run's own
  evidence, without PSS/E and without reading a 90 MB file end to end -- and
  writes the marker where the evidence says the run finished.

WHAT COUNTS AS EVIDENCE, strongest first
  1. THE TIME AXIS of the .out itself. Where the folder holds an
     OUT_LAYOUT_<n>.txt (the packed reader writes one per channel set), the
     last sample of the time column is read with three seeks. That is the same
     test the study makes, and it is decisive: the run either reached the end
     of the simulation or it did not.
  2. A SCORE ALREADY ON DISK. parts\\SCEN_<id>.csv is written by whatever
     process scored the scenario, and nothing scores a scenario it did not
     read to the end. Its verdict line is proof the run finished.
  3. THE STUDY'S OWN PROGRESS RECORD. logs\\PROGRESS*.csv carries one row per
     scenario event; a DONE row for this id is the run saying so itself.
  4. SIZE, ONLY AS CORROBORATION AND ONLY WITH --use-size. A .out is
     fixed-width records, so a complete run is within a few per cent of its
     neighbours -- but a folder can hold two channel sets (14-area and 20-area
     runs differ by ~20 MB) and judging those against one median is what
     wrongly retired 182 complete base runs before. Off by default.

WHAT IT WRITES
  <id>.done      when the evidence says the run reached the end. The marker
                 carries the clearing time (from the fault list, so the report
                 can score it without re-reading anything) and tend=.
  <id>.partial   when the run stopped early but reached PARTIAL_MIN_FRAC of
                 the simulation -- the engine's own partial-run marker, so the
                 result is drawn, scored and labelled PARTIAL rather than
                 silently dropped. Never a .done: a re-run still replaces it.
  nothing        when the evidence says the run did not get far enough, or
                 when there is no evidence at all. Those are named on screen.

  Nothing is deleted and no .out is modified. A scenario that already has a
  .done is left exactly as it is.

HOW TO USE
  1. Put this file in C:\\KV beside z4_cmp_all_con.py.
  2. Run it. It lists what it WOULD do and writes nothing:
         python z4_mark_done_con.py
  3. Look at the table. Then write the markers:
         python z4_mark_done_con.py --write
  4. Now PIPELINE = "all" re-simulates only what genuinely did not finish, and
     the report phase scores the rest.

  Other switches:
      --only SantaFe,IronStar     just these projects (no prompt)
      --all                       every project (no prompt)
      --base / --proj             just one case
      --use-size                  allow the size test where nothing better exists
      --restore-stale             put back <id>.out.stale_<stamp> and its
                                  markers (the launcher moves a scenario aside
                                  when its marker does not match the fault list)
      --undo                      remove only the markers THIS script wrote
                                  (each one it writes is signed)

Python 3.4, standard library only.
"""
import os
import re
import sys
import csv
import glob
import time
import struct

# ============================================================================
#  SETTINGS -- the defaults match z4_cmp_all_con.py
# ============================================================================
STUDY_ROOT = r"C:\KV"
BASE_DIR   = os.path.join(STUDY_ROOT, "Base")
PROJ_DIR   = os.path.join(STUDY_ROOT, "Projects")
RESULTS_BASE = "results_base"          # folder under BASE_DIR
RESULTS_PROJ = "results_proj"          # folder under PROJ_DIR

PROJECTS = []                          # WHICH PROJECTS TO MARK. Leave it empty
                                       # and the script lists what it found and
                                       # asks. Name them here to skip the
                                       # question, e.g. ["IronStar"] or
                                       # ["SantaFe", "IronStar"]. --only on the
                                       # command line wins over this, --all
                                       # takes every project without asking.

SIM_END_S        = 25.2                # the run length the study asked for
END_TOL_S        = 0.11                # within this of SIM_END_S = finished
PARTIAL_MIN_FRAC = 0.80                # of SIM_END_S; shorter = no marker
USE_SIZE         = False               # --use-size: allow the size fallback
SIZE_FRAC        = 0.95                # of the median of the folder's OWN
                                       # marked-done files, and only when the
                                       # folder holds one channel set
SIGNATURE        = "z4_mark_done_con"  # written into every marker this makes
# ============================================================================

_F32 = struct.Struct("<f")
_U32 = struct.Struct("<I")


# ---------------------------------------------------------------- the .out --
def _layouts_in(outs_dir):
    """Every OUT_LAYOUT_<n>.txt in this folder as (stride, base, time offset,
       trailer words), the packed reader's own description of a channel set."""
    out = []
    for p in sorted(glob.glob(os.path.join(outs_dir, "OUT_LAYOUT*.txt"))):
        try:
            txt = open(p, "r", errors="replace").read()
        except Exception:
            continue
        if not re.search(r"^verified\s*=\s*yes", txt, re.M):
            continue
        m_s = re.search(r"^stride\s*=\s*(\d+)", txt, re.M)
        m_b = re.search(r"^base\s*=\s*(\d+)", txt, re.M)
        m_t = re.search(r"^off\s+time\s*=\s*(\d+)", txt, re.M)
        m_r = re.search(r"^trailer\s*=\s*(\d+)", txt, re.M)
        if not (m_s and m_b and m_t):
            continue
        out.append((int(m_s.group(1)), int(m_b.group(1)), int(m_t.group(1)),
                    int(m_r.group(1)) if m_r else 0))
    return out


def _word(fh, idx):
    """The float32 at word index idx, or None past the end / non-finite."""
    try:
        fh.seek(4 * idx)
        b = fh.read(4)
        if len(b) < 4:
            return None
        if (_U32.unpack(b)[0] >> 23) & 0xFF == 0xFF:      # NaN / Inf
            return None
        return _F32.unpack(b)[0]
    except Exception:
        return None


def _looks_like_time(vals):
    """True when these samples behave like the study's time column: starting
       near zero and rising by one roughly constant step."""
    if len(vals) < 4 or any(v is None for v in vals):
        return False
    if not (-1.0 <= vals[0] <= 1.0):
        return False
    step = vals[1] - vals[0]
    if not (1e-6 < step < 10.0):
        return False
    for a, b in zip(vals, vals[1:]):
        d = b - a
        if d < -1e-9 or d > max(10.0 * step, 1.0):
            return False
    return True


def _end_time_from_layout(path, lay):
    """The last simulated second in this .out under this layout, or None when
       the layout does not describe this file."""
    stride, base, t_off, trailer = lay
    if stride <= 0:
        return None
    try:
        words = os.path.getsize(path) // 4
    except Exception:
        return None
    try:
        fh = open(path, "rb")
    except Exception:
        return None
    try:
        # Does the time column behave like time at the head of the file?
        head = [_word(fh, base + t_off + i * stride) for i in range(8)]
        if not _looks_like_time(head):
            return None
        # WHICH RECORD IS THE LAST ONE, ASKED OF THE GRID ITSELF.
        #
        # Time is a ramp: sample i is t0 + i*step, and the first eight above
        # give both. So a word is part of the time column only if it equals
        # what the grid says it should be at that index -- nothing else does.
        #
        # THE LOOSE TEST READ VOLTAGES AS SECONDS. It accepted any value whose
        # neighbour was within one step, and a bus voltage sitting at 0.97 pu
        # passes that trivially: every .out in a folder, 21 MB and 95 MB
        # alike, came back "0.97 s" and 197 complete runs were called too
        # short. The tail of the file is where that happens -- the record
        # count from the byte size can overshoot into the trailer, and one
        # word past the grid the stride lands in some other channel.
        #
        # Binary search for the largest index that still matches the grid:
        # inside the data it matches, past the end it does not, and index 0
        # is known good from the head. About twenty seeks, no walk.
        step = head[1] - head[0]
        t0 = head[0]
        tol = max(0.5 * step, 1e-4)

        def _on_grid(i):
            v = _word(fh, base + t_off + i * stride)
            return v is not None and abs(v - (t0 + i * step)) <= tol

        hi = int((words - trailer - base - t_off) // stride) - 1
        if hi < 0:
            return None
        if _on_grid(hi):
            return t0 + hi * step
        lo = 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _on_grid(mid):
                lo = mid
            else:
                hi = mid - 1
        return t0 + lo * step if lo > 0 else None
    finally:
        try:
            fh.close()
        except Exception:
            pass


def end_time_of(path, layouts):
    """(seconds, how) for this .out -- the time axis under whichever layout
       fits it -- or (None, "")."""
    for lay in layouts:
        t = _end_time_from_layout(path, lay)
        if t is not None:
            return t, "time axis (stride %d)" % lay[0]
    return None, ""


# ------------------------------------------------------------ other proof --
def scored_ids(rdir):
    """Scenarios whose parts\\SCEN_<id>.csv carries a verdict. Nothing scores
       a scenario it did not read to the end."""
    got = set()
    for p in glob.glob(os.path.join(rdir, "parts", "SCEN_*.csv")):
        nm = os.path.basename(p)
        if nm.upper().endswith("_MEAS.CSV"):
            continue
        sid = nm[len("SCEN_"):-len(".csv")]
        try:
            with open(p) as fh:
                for ln in fh:
                    if ln.startswith("verdict,"):
                        got.add(sid)
                        break
        except Exception:
            continue
    return got


def progress_done_ids(rdir):
    """Scenarios the study's own progress record calls DONE."""
    got = set()
    for p in glob.glob(os.path.join(rdir, "logs", "PROGRESS*.csv")):
        try:
            with open(p) as fh:
                for row in csv.reader(fh):
                    if len(row) >= 3 and str(row[2]).strip().upper() == "DONE":
                        got.add(str(row[1]).strip())
        except Exception:
            continue
    return got


def _row_sig_fn(case_key):
    """The study's OWN _fault_row_sig, lifted out of the engine script.

       THE MARKER NEEDS THE FINGERPRINT OR IT IS WORSE THAN NOTHING. A .done
       carries sig=<hash of the fault row>, and the launcher checks it before
       trusting the marker: one without a fingerprint is moved aside as .stale
       and the scenario is SIMULATED AGAIN -- exactly what this script exists
       to avoid. So the hash is not reimplemented here, where it could drift
       out of step with the study; the function is read from the engine that
       wrote the results and executed as it stands."""
    for d, name in ((BASE_DIR, "z4_spp_b_con.py"), (PROJ_DIR, "z4_spp_p_con.py"),
                    (STUDY_ROOT, "z4_spp_b_con.py"), (STUDY_ROOT, "z4_spp_p_con.py")):
        if case_key == "BASE" and "p_con" in name:
            continue
        if case_key == "PROJ" and "b_con" in name:
            continue
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        try:
            src = open(p, "r", errors="replace").read()
        except Exception:
            continue
        i = src.find("def _fault_row_sig(r):")
        if i < 0:
            continue
        j = src.find("\ndef ", i + 1)
        body = src[i:j if j > 0 else len(src)]
        g = {"hashlib": __import__("hashlib")}
        try:
            exec(body, g)
            fn = g.get("_fault_row_sig")
            if fn:
                print("   fingerprint: _fault_row_sig() read from %s" % p)
                return fn
        except Exception as e:
            print("   fingerprint: could not use %s (%s)" % (p, e))
    print("   fingerprint: NOT AVAILABLE -- markers will carry no sig=, and the")
    print("                launcher re-runs a marker without one. Put the study")
    print("                script beside the case folder and run this again.")
    return None


def _fault_list_paths(rdir, proj):
    """The fault lists in the order the LAUNCHER reads them. It prefers the
       shared DISIS list at the study root and falls back to the folder's own
       copy -- and the two can differ. A fingerprint computed from the wrong
       one does not match the marker the launcher expects, and the launcher
       then moves the .out ASIDE as .stale and simulates the fault again."""
    paths = []
    if proj:
        paths.append(os.path.join(STUDY_ROOT, "SPP_FAULTS_CON_%s.csv" % proj))
    paths.append(os.path.join(rdir, "faults", "SPP_FAULTS.csv"))
    return [p for p in paths if os.path.isfile(p)]


def fault_rows(rdir, proj=""):
    """{fault id: the row as the study read it}, from the first list that has
       rows -- the same order the launcher uses."""
    for p in _fault_list_paths(rdir, proj):
        out = {}
        try:
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    fid = (r.get("fault_id") or "").strip()
                    if fid:
                        out[fid] = r
        except Exception:
            continue
        if out:
            return out
    return {}


def sig_self_check(outs_dir, rows, sig_fn):
    """(ok, message). Recompute the fingerprint of a fault the ENGINE already
       marked, and compare it with what the engine wrote.

       THIS IS NOT OPTIONAL. A marker whose sig= does not match what the
       launcher computes is treated as a marker for a DIFFERENT fault list:
       the launcher moves the .out, the .done and the attempts aside as
       .stale_<stamp> and simulates the fault again. Writing a whole folder of
       such markers takes a finished study apart. So if the check fails,
       nothing is written and the reason is printed."""
    if not rows or sig_fn is None:
        return True, "no fault list or no fingerprint function -- markers carry no sig="
    for p in sorted(glob.glob(os.path.join(outs_dir, "*.done"))):
        sid = os.path.basename(p)[:-len(".done")]
        if sid not in rows:
            continue
        have = ""
        try:
            for ln in open(p):
                if ln.startswith("sig="):
                    have = ln[4:].strip()
                    break
        except Exception:
            continue
        if not have:
            continue
        try:
            want = sig_fn(rows[sid])
        except Exception as e:
            return False, "the fingerprint function raised on %s: %s" % (sid, e)
        if want == have:
            return True, "checked against %s -- the fingerprint matches" % sid
        return False, ("%s was marked by the engine with sig=%s, but this fault "
                       "list gives sig=%s. The list this folder was RUN with is "
                       "not the list being read here, so every marker written "
                       "now would be rejected and its .out moved aside as "
                       ".stale. Point STUDY_ROOT at the right study, or delete "
                       "the stale SPP_FAULTS_CON_*.csv." % (sid, have, want))
    return True, "no engine-written marker carries a sig= to check against"


def restore_stale(outs_dir, write=False):
    """Bring back <id>.out.stale_<stamp> and its markers.

       The launcher moves a scenario aside when its marker does not match the
       fault list. Nothing is deleted, so a folder emptied that way is put
       back exactly: the NEWEST stamp of each scenario wins, and a file that
       already exists under its plain name is left alone."""
    best = {}
    for p in glob.glob(os.path.join(outs_dir, "*.stale_*")):
        base = os.path.basename(p)
        i = base.rfind(".stale_")
        if i < 0:
            continue
        plain, stamp = base[:i], base[i + len(".stale_"):]
        prev = best.get(plain)
        if prev is None or stamp > prev[0]:
            best[plain] = (stamp, p)
    n = 0
    held = []
    for plain in sorted(best):
        stamp, p = best[plain]
        tgt = os.path.join(outs_dir, plain)
        if os.path.exists(tgt):
            continue
        # A MARKER IS NOT RESTORED ON TOP OF A DIFFERENT RUN'S .out.
        #
        # The sweep that moved these aside then started simulating them again,
        # and an interrupted run leaves a HALF-WRITTEN .out under the plain
        # name. Its scenario's .out is therefore skipped above (the target
        # exists) while its .done would come back -- a marker from the old
        # COMPLETE run sitting beside a new truncated one, which every later
        # step reads as a finished result. So a marker is held back whenever
        # the .out beside it is not the one it was moved aside with.
        stem, ext = os.path.splitext(plain)
        if ext.lower() != ".out":
            out_now = os.path.join(outs_dir, stem + ".out")
            out_back = os.path.join(outs_dir, stem + ".out.stale_" + stamp)
            if os.path.isfile(out_now) and not os.path.isfile(out_back):
                held.append(plain)
                continue
        print("   %-28s <- %s" % (plain, os.path.basename(p)))
        if write:
            try:
                os.rename(p, tgt)
                n += 1
            except Exception as e:
                print("   %-28s could not be restored: %s" % (plain, e))
    if held:
        print("")
        print("   HELD BACK -- their .out was re-simulated after the move, so this")
        print("   marker describes a different run and is left where it is:")
        for plain in held:
            print("       %s" % plain)
        print("   Those .out files are judged on their own time axis by an ordinary")
        print("   run of this script (no --restore-stale).")
    return n


def tclear_of(rdir):
    """{fault id: clearing time} from this folder's own fault list, so a marker
       this script writes lets the report score the fault without re-reading
       anything. PRE_FAULT_S + cycles/60, the study's own arithmetic."""
    out = {}
    for name in ("SPP_FAULTS.csv",):
        p = os.path.join(rdir, "faults", name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    fid = (r.get("fault_id") or "").strip()
                    if not fid:
                        continue
                    for k in ("tclear_s", "tclear"):
                        v = (r.get(k) or "").strip()
                        if v:
                            try:
                                out[fid] = float(v)
                            except ValueError:
                                pass
                            break
                    else:
                        cyc = (r.get("cycles") or "").strip()
                        pre = (r.get("pre_fault_s") or "").strip() or "5.0"
                        try:
                            out[fid] = float(pre) + float(cyc) / 60.0
                        except ValueError:
                            pass
        except Exception:
            continue
    return out


def _median(v):
    v = sorted(v)
    if not v:
        return 0
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2.0


def size_reference(outs_dir):
    """(median size of the files that ARE marked done, one channel set?) --
       the size fallback is offered only when the marked files agree with each
       other to within 5 %, which is what 'one channel set' means here."""
    sizes = []
    for p in glob.glob(os.path.join(outs_dir, "*.out")):
        sid = os.path.splitext(os.path.basename(p))[0]
        if sid.upper().startswith("FLAT"):
            continue
        if os.path.isfile(os.path.join(outs_dir, sid + ".done")):
            try:
                sizes.append(os.path.getsize(p))
            except Exception:
                pass
    if len(sizes) < 3:
        return 0, False
    med = _median(sizes)
    tight = all(abs(s - med) <= 0.05 * med for s in sizes)
    return med, tight


# ------------------------------------------------------------------ folders --
def results_folders(only_projects=None, want_base=True, want_proj=True):
    """Every <case>/results_*/<project>_<mode> folder that holds an outs\\."""
    out = []
    roots = []
    if want_base:
        roots.append(("BASE", os.path.join(BASE_DIR, RESULTS_BASE)))
    if want_proj:
        roots.append(("PROJ", os.path.join(PROJ_DIR, RESULTS_PROJ)))
    for key, root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            rdir = os.path.join(root, name)
            if not os.path.isdir(os.path.join(rdir, "outs")):
                # one level deeper: a dated or tagged copy
                for sub in sorted(os.listdir(rdir) if os.path.isdir(rdir) else []):
                    d2 = os.path.join(rdir, sub)
                    if not os.path.isdir(os.path.join(d2, "outs")):
                        continue
                    # THE FILTER APPLIES HERE TOO. A tagged copy such as
                    # results_proj\Sep21_full gia\SantaFe_spp is still SantaFe.
                    if only_projects and sub.split("_")[0].lower() not in only_projects:
                        continue
                    out.append((key, sub, d2))
                continue
            proj = name.split("_")[0]
            if only_projects and proj.lower() not in only_projects:
                continue
            out.append((key, name, rdir))
    return out


def project_names(want_base=True, want_proj=True):
    """Every project that has a results folder, in the spelling the folder uses."""
    seen = {}
    for _key, name, _rdir in results_folders(None, want_base, want_proj):
        p = name.split("_")[0]
        seen.setdefault(p.lower(), p)
    return [seen[k] for k in sorted(seen)]


def ask_projects(want_base, want_proj):
    """List the projects found and let the user pick. Returns a lower-case set,
       or None for 'every project'. Anything other than a live console -- a
       scheduled run, a pipe -- takes every project rather than hanging."""
    names = project_names(want_base, want_proj)
    if not names:
        return None
    print("")
    print(" PROJECTS FOUND:")
    for i, p in enumerate(names, 1):
        print("     %d. %s" % (i, p))
    print("     0. all of them")
    try:
        if not sys.stdin.isatty():
            raise EOFError
        raw = input(" Which project? (number, name, or several separated by commas) ")
    except (EOFError, KeyboardInterrupt, AttributeError):
        print(" (no console -- taking every project)")
        return None
    raw = (raw or "").strip()
    if not raw or raw == "0" or raw.lower() in ("all", "a", "*"):
        return None
    picked = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit() and 1 <= int(tok) <= len(names):
            picked.add(names[int(tok) - 1].lower())
        else:
            picked.add(tok.lower())
    unknown = sorted(p for p in picked if p not in set(n.lower() for n in names))
    if unknown:
        print(" [mark] no results folder for: %s" % ", ".join(unknown))
    return picked or None


# -------------------------------------------------------------- the decision --
def judge_folder(key, name, rdir, write=False, use_size=False):
    """Decide every unmarked .out in one folder. Returns a counts dict."""
    outs_dir = os.path.join(rdir, "outs")
    lays = _layouts_in(outs_dir)
    scored = scored_ids(rdir)
    progd = progress_done_ids(rdir)
    tcl = tclear_of(rdir)
    proj = str(name or "").split("_")[0]
    rows = fault_rows(rdir, proj)
    sigfn = _row_sig_fn(key) if rows else None
    med, tight = size_reference(outs_dir)
    t_full = float(SIM_END_S) - float(END_TOL_S)
    t_part = float(PARTIAL_MIN_FRAC) * float(SIM_END_S)

    print("")
    print("=" * 96)
    print(" %-5s %-28s %s" % (key, name, rdir))
    print("=" * 96)
    print("   layouts: %d   scored parts: %d   progress DONE: %d   fault list: %d"
          % (len(lays), len(scored), len(progd), len(tcl)))
    if not lays:
        print("   NOTE: no OUT_LAYOUT*.txt here, so the time axis cannot be read.")
        print("         The other evidence still applies; run a plot pass once to")
        print("         create a layout if you want the decisive test.")
    if use_size:
        print("   size reference: %s"
              % (("%.0f MB median, one channel set" % (med / 1e6)) if (med and tight)
                 else "not usable (the marked files do not agree within 5 %)"))
    # THE FINGERPRINT IS CHECKED BEFORE ANYTHING IS WRITTEN.
    ok_sig, why_sig = sig_self_check(outs_dir, rows, sigfn)
    print("   fingerprint: %s" % why_sig)
    if not ok_sig:
        print("")
        print("   *** NO MARKER IS WRITTEN IN THIS FOLDER ***")
        print("   A marker the launcher does not recognise is worse than no marker:")
        print("   it moves the .out, the .done and the .attempts aside as")
        print("   .stale_<stamp> and simulates the fault again. To put back a")
        print("   folder that has already been emptied that way:")
        print("       python %s --restore-stale --write" % os.path.basename(__file__))
        return {"done": 0, "partial": 0, "short": 0, "nothing": 0, "already": 0}

    # IS THE TIME AXIS ACTUALLY READABLE IN THIS FOLDER?
    #
    # A .out is not one flat grid of records -- the engine's own reader has a
    # RESYNC step for it -- so a layout that fits the head can stop fitting
    # further in. When it does, the search for the last sample stops at the
    # first break and every file in the folder reports the SAME end time:
    # 0.97 s for 21 MB crashes and 95 MB complete runs alike, and 194 finished
    # runs judged "too short".
    #
    # That agreement is the tell. Runs that stop at their own instant do not
    # land on one value. So the end times are read first, and if a quarter of
    # the folder shares one, the reader is wrong HERE and is not used: the
    # scored parts and the progress record decide instead, and nothing is
    # marked on a number this script does not trust.
    if lays:
        _seen = {}
        for _p in glob.glob(os.path.join(outs_dir, "*.out")):
            _t, _ = end_time_of(_p, lays)
            if _t is not None:
                _k = round(_t, 2)
                _seen[_k] = _seen.get(_k, 0) + 1
        _n_read = sum(_seen.values())
        if _n_read >= 8:
            _top, _cnt = max(_seen.items(), key=lambda kv: kv[1])
            if _cnt >= max(4, int(0.25 * _n_read)):
                print("   TIME AXIS NOT USED HERE: %d of %d .out file(s) all read "
                      "%.2f s." % (_cnt, _n_read, _top))
                print("         Files of every size cannot have stopped at the same "
                      "instant, so")
                print("         the layout stops fitting part way through this "
                      "folder's files.")
                print("         Deciding on the scored parts and the progress record "
                      "instead.")
                lays = []

    n = {"done": 0, "partial": 0, "short": 0, "nothing": 0, "already": 0}
    table = []
    for p in sorted(glob.glob(os.path.join(outs_dir, "*.out")),
                    key=lambda q: (len(os.path.basename(q)), q)):
        sid = os.path.splitext(os.path.basename(p))[0]
        if os.path.isfile(os.path.join(outs_dir, sid + ".done")):
            n["already"] += 1
            continue
        is_flat = sid.upper().startswith("FLAT")
        try:
            mb = os.path.getsize(p) / 1e6
        except Exception:
            mb = 0.0

        verdict, how, tend = None, "", None
        t, how_t = end_time_of(p, lays)
        if t is not None:
            tend = t
            how = how_t
            verdict = "done" if (is_flat or t >= t_full) else (
                "partial" if t >= t_part else "short")
        elif sid in scored:
            verdict, how = "done", "already scored (parts\\SCEN_%s.csv)" % sid
        elif sid in progd:
            verdict, how = "done", "the study's progress record says DONE"
        elif use_size and med and tight and os.path.getsize(p) >= SIZE_FRAC * med:
            verdict, how = "done", "%.0f MB vs %.0f MB median (one channel set)" % (mb, med / 1e6)
        else:
            verdict, how = "nothing", "no evidence either way"

        n[verdict] += 1
        table.append((sid, mb, verdict, how, tend))

        if not write or verdict not in ("done", "partial"):
            continue
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        _sig = ""
        if sigfn is not None and sid in rows:
            try:
                _sig = sigfn(rows[sid])
            except Exception:
                _sig = ""
        if verdict == "done":
            txt = ("" if tcl.get(sid) is None else repr(float(tcl[sid])))
            if _sig:
                txt += "\nsig=%s" % _sig
            if tend is not None:
                txt += "\ntend=%.3f" % float(tend)
            txt += "\nby=%s %s\nwhy=%s" % (SIGNATURE, stamp, how)
            _write(os.path.join(outs_dir, sid + ".done"), txt)
        else:
            txt = "tend=%.3f" % float(tend)
            if _sig:
                txt += "\nsig=%s" % _sig
            txt += "\nby=%s %s\nwhy=%s" % (SIGNATURE, stamp, how)
            _write(os.path.join(outs_dir, sid + ".partial"), txt)

    for sid, mb, v, how, tend in table:
        mark = {"done": "-> .done   ", "partial": "-> .partial",
                "short": "   left    ", "nothing": "   left    "}[v]
        te = ("%6.2f s" % tend) if tend is not None else "   --  "
        print("   %-12s %7.1f MB  %s  %s  %s"
              % (sid, mb, te, mark, how))
    print("   %d already marked | %d -> .done | %d -> .partial | %d too short | %d no evidence"
          % (n["already"], n["done"], n["partial"], n["short"], n["nothing"]))
    return n


def _write(path, text):
    try:
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w") as fh:
            fh.write(text)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception as e:
        print("   *** could not write %s: %s" % (path, e))


def undo_folder(key, name, rdir):
    """Remove only the markers this script wrote -- each carries its name."""
    outs_dir = os.path.join(rdir, "outs")
    n = 0
    for ext in ("done", "partial"):
        for p in glob.glob(os.path.join(outs_dir, "*." + ext)):
            try:
                with open(p) as fh:
                    if SIGNATURE not in fh.read():
                        continue
                os.remove(p)
                n += 1
            except Exception:
                continue
    print(" %-5s %-28s %d marker(s) written by this script removed" % (key, name, n))
    return n


def main(argv):
    write = "--write" in argv
    use_size = "--use-size" in argv or USE_SIZE
    undo = "--undo" in argv
    want_base = "--proj" not in argv
    want_proj = "--base" not in argv
    only = None
    asked = False
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = set(x.strip().lower() for x in argv[i + 1].split(",") if x.strip())
            asked = True
    if only is None and "--all" in argv:
        asked = True                       # every project, no question
    if only is None and not asked and PROJECTS:
        only = set(str(p).strip().lower() for p in PROJECTS if str(p).strip())
        asked = True
    if only is None and not asked:
        only = ask_projects(want_base, want_proj)

    folders = results_folders(only, want_base, want_proj)
    if not folders:
        print("[mark] no results folders found under %s / %s"
              % (os.path.join(BASE_DIR, RESULTS_BASE),
                 os.path.join(PROJ_DIR, RESULTS_PROJ)))
        if only:
            print("[mark] the pick was: %s -- the folders present are: %s"
                  % (", ".join(sorted(only)),
                     ", ".join(project_names(want_base, want_proj)) or "(none)"))
        return 1

    print("")
    print("=" * 96)
    print(" MARK FINISHED RUNS -- %s" % ("WRITING markers" if write or undo
                                         else "DRY RUN, nothing is written"))
    print(" run length %.2f s, finished at %.2f s or later, partial from %.2f s"
          % (SIM_END_S, SIM_END_S - END_TOL_S, PARTIAL_MIN_FRAC * SIM_END_S))
    print(" projects: %s   case: %s   folders: %d"
          % ("all" if not only else ", ".join(sorted(only)),
             "base + project" if (want_base and want_proj)
             else ("base only" if want_base else "project only"), len(folders)))
    print("=" * 96)

    if "--restore-stale" in argv:
        tot = 0
        for key, name, rdir in folders:
            od = os.path.join(rdir, "outs")
            print("")
            print("=" * 96)
            print(" %-5s %-28s %s" % (key, name, od))
            print("=" * 96)
            tot += restore_stale(od, write=write)
        if write:
            print("\n[mark] %d file(s) restored." % tot)
        else:
            print("\n[mark] nothing was moved. Add --write to restore them.")
        return 0

    if undo:
        tot = 0
        for key, name, rdir in folders:
            tot += undo_folder(key, name, rdir)
        print("\n[mark] %d marker(s) removed." % tot)
        return 0

    tot = {"done": 0, "partial": 0, "short": 0, "nothing": 0, "already": 0}
    for key, name, rdir in folders:
        n = judge_folder(key, name, rdir, write=write, use_size=use_size)
        for k in tot:
            tot[k] += n[k]

    print("")
    print("=" * 96)
    print(" TOTAL: %d already marked | %d finished | %d partial | %d too short | %d no evidence"
          % (tot["already"], tot["done"], tot["partial"], tot["short"], tot["nothing"]))
    print("=" * 96)
    if not write:
        print(" Nothing was written. Run it again with --write to create the markers.")
    else:
        print(" Markers written. PIPELINE = \"all\" will now re-simulate only the runs")
        print(" that genuinely did not finish, and the report phase can score the rest.")
        print(" To take them back: python %s --undo" % os.path.basename(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
