# PSS/E 35 (v35) script set: handoff

## Where things are
- **Repo:** `venky228/venky228`, branch `claude/great-ramanujan-c0v50u`.
- **v35 set:** `z7_psse35/`. It holds 14 scripts, each named `z7_*_v35.py`, and they reference each other by the `_v35` names.
  - Root: `z7_main_v35.py`, `z7_spike_find_v35.py`, `z7_cmp_multi_v35.py`, `z7_fault_list_v35.py`, `z7_gt_report_v35.py`, `z7_poi_distance_v35.py`, `z7_probe_psse_v35.py`, `z7_scr_v35.py`, `z7_tidy_results_v35.py`, `z7_init_check_v35.py`
  - `Base\`: `z7_lch_b_v35.py`, `z7_spp_b_v35.py`
  - `Projects\`: `z7_lch_p_v35.py`, `z7_spp_p_v35.py`
- **v34 (live study):** `z7_con/`, which is generated from `z6_con/` with `mk_z7.py`, a plain `z6_` to `z7_` rename. Edit v34 in `z6_con`, then regenerate `z7_con`.
- **Target machine:**
  - v34 study: `C:\KV\ENGIE\CQ_MIT`, run with `C:\Python34\python.exe` (32-bit) and PSS/E 34 at `C:\Program Files (x86)\PTI\PSSE34`.
  - PSS/E 35.6: installed at `C:\Program Files\PTI\PSSE35\35.6`. It has PSSPY27/37/38/39/311, PSSBIN, PSSBIN32 and DSUSR-2013/2017.
  - v35 study: planned for a separate folder, `C:\KV\ENGIE\CQ_MIT_35`, so PSS/E 34 and 35 results are never mixed in one comparison.

## How v35 differs from z7 (v34), as of commit 7f943ad
1. **PSS/E path discovery** (`spp_b`, `spp_p`, `spike_find`, `fault_list`):
   - `_psse_install_roots()` also searches one level down (`PSSE35\35.x`).
   - It orders installs by the interpreter's bitness: 64-bit Python looks at Program Files first, 32-bit at Program Files (x86) first.
   - The `PSSE_ROOT` environment variable always wins.
2. **Loading PSS/E:**
   - `_import_psse_shim()` tries `psse35`, then `psse34`.
   - `_psse_dll_dir()` calls `os.add_dll_directory`, which Python 3.8+ needs to load the PSS/E DLLs.
3. **Launchers** (`lch_b`, `lch_p`): `preflight_python` (the wrong-Python check) also sees `PSSE35\35.x\PSSPY##`, and suggests `C:\Program Files\Python##` and `py -3.9`.
4. **User-model compile step:**
   - `DYR_COMPILE_BAT_NAMES` looks for `MyCompile35.bat` first, then `MyCompile34.bat`.
   - In `z7_main_v35.py`, `DYR_COMPILE_BATS = ["MyCompile35.bat", "MyCload41.bat"]`.
5. **Floating-point exception mask:** tries `ucrtbase` first (the C runtime of 64-bit Python and PSS/E 35).
6. **Messages:** `taskkill /F /IM psse35.exe` hints, `C:\Python39\python.exe` in the run instructions, and "PSS/E 35 python API" in a comment.
7. **Panel settings** in `z7_main_v35.py` are the user's upload of 2026-09-28: `RUN_CASES = "base"`, `ONLY_FAULTS = ["F01-F04"]`, `FLAT_RUN_S = 5`, `PRE_FAULT_S = 3`, `SIM_END_S = 8`, `EGF_OFF_BASE_RUN = False`, `EGF_FIRST = False`, `FORCE_RESCORE = False`, `PLOT_MISSING_OUTS = False`; `PIPELINE` is still `"all"`.
8. **DLL bitness guard** (`spp_b`, `spp_p`, the same patch in both). PSS/E 35 is 64-bit and loads `dsusr.dll` from the case folder inside `psseinit()`.
   - At startup, before psspy is imported, a `dsusr.dll` of the wrong bitness is moved aside as `dsusr.dll.32bit`, and the build compiles a new one. If it cannot be moved, the process exits with rc=1 before PSS/E starts.
   - After the compile, a 32-bit `dsusr.dll` (the `.bat` files still set up for PSS/E 34) is moved aside, the previous one is put back, and the build stops, naming the `.bat` files.
   - Model DLLs named in `add_library.idv`, all `*.dll` (`load_user_dlls`), and the BESS DLLs are checked before `addmodellibrary`. A wrong one stops the run and is named. The BESS scan ignores wrong-bitness copies.
9. **`z7_init_check_v35.py`** (study root, reads only; v4).
   - It counts the `.dll` in `Base\\` and `Projects\\` by bitness.
   - It prints the versions of `psse35.exe`, `GIC.dll`, `MUSTENG.dll`, the `.pyd` files and the CodeMeter runtime, and lists the PSSBIN DLLs that another folder would supply first.
   - It starts PSS/E in an empty folder, one process per test, running the child from a file. The parent polls the child's module list (`EnumProcessModulesEx`, every 0.1 s) and prints the runtime libraries used and any DLL loaded from outside the PSS/E, Python and Windows folders.
   - The tests:
     1. as the study starts it;
     2. `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `KMP_AFFINITY=disabled`;
     3. the same plus `SetProcessAffinityMask(1)`;
     4. a clean environment: Windows variables only, PATH = System32, Windows, Python;
     5. every other 64-bit Python (`py -0p`, `C:\\Python3*`, `%LOCALAPPDATA%\\Programs\\Python`) that has a `PSSPY<xy>` folder.
   - The first test that starts is repeated beside `Base\\dsusr.dll`.
   - Then it runs `psse35.exe` itself for 30 s: still up means the GUI starts; a crash means the GUI crashes too.
   - It reads the event log and prints a verdict: GUI CRASHES TOO / THREADS / ENVIRONMENT / PYTHON VERSION / GUI starts but Python doesn't.
   - The v3 tests (`SetDllDirectory`, pre-loading, a `python.exe` copy with the newer Visual C++ runtime) were removed after they failed on the user's PC.

## v34 fixes made after the v35 set, NOT in v35
The user asked for the first two to be v34-only. Ask before carrying any of them to v35.

| Commit | Change | Files |
|---|---|---|
| b47d23a | **F167 plotter fix.** A drawn run that finished but holds non-finite values now gets a `.plotted` marker with `[endtime-checked]`, and `_nonfinite` is reset for each scenario. Before, such a run shut down plotter slots as "failing at startup". | `spp_b`, `spp_p` |
| 0de52e0 | **PDF violations index headers.** "Quantity" became "Signal" and "What it broke" became "Violation". | `spp_b`, `spp_p` |
| 117dfda | **Compare memory fix.** `_release_compare_memory()` clears `_MEAS_CACHE`, `_SCEN_PART_CACHE`, `_OUT_SET_CACHE` and `_PART_LAY_CACHE`, then runs `gc.collect()`, before `compare_surplus_scenarios`, `compare_egf_variants`, `compare_three_way` and `write_all_variants_overvoltage`. If no thread can be started, the 3-way child's output is drained in the main thread. It fixes the empty `()` errors and "can't start new thread" in 32-bit Python. It is less critical under 64-bit Python 3.9 (v35), but harmless. | `z7_main` |

## Testing status of v35
- All 13 scripts compile under Python 3.11.
- Install discovery was tested on fake 34/35 folder trees: the order is correct for 32-bit and 64-bit Python, and `PSSE_ROOT` wins.
- The psse35/psse34 fallback was tested.
- An early fake-PSS/E e2e run of the four patched loader scripts, before the rename, reached the .sav-build stage (rc=0). It then stopped there, because the panel default stops after building the .sav files.
- DLL guard: tested by importing the real `spp_b`/`spp_p` against a fake psspy. Covered: the `.idv` parsing (comments, quotes, spaces, absolute paths), stopping before any `addmodellibrary`/`runrspnsfile`, the BESS scan and pin, a 32-bit and a 64-bit compile result, startup with a 32-bit, 64-bit and non-DLL `dsusr.dll`, three processes racing, and a failed move.
- `z7_init_check_v35.py`: tested with a fake install. Covered: a crash on dsusr.dll, a vendor DLL only, no PSS/E, PSS/E failing everywhere, a hang (timeout), and no case folders.
- **First real PSS/E 35.6 run (2026-09-28)**, Python 3.11 at `C:\Users\conti\AppData\Local\Programs\Python\Python311`, found `PSSPY311`:
  - The BASE build exited `rc=3221225477` (0xC0000005, access violation) on all 3 launches, inside `psseinit(150000)`, right after the copyright banner. That is before the case loads; "150000 BUS POWER SYSTEM SIMULATOR" never printed.
  - `INIT_CHECK.txt` (v1): PSS/E dies in `psseinit(150000)` **even in an empty folder**, so the cause is not the study or `dsusr.dll`.
    - Windows log for each start: `GIC.dll` 0xC00000FD (stack overflow), then `MUSTENG.dll` 0xC0000005, then `ntdll.dll` 0xC0000005, all from `...\PSSE35\35.6\PSSBIN`.
    - Main suspect: `python.exe`'s ~2 MB main-thread stack.
    - The v2 check was sent to test it; result pending.
  - `INIT_CHECK.txt` (v2, 09:47): PSS/E failed to start every way tried.
    - Test cases: the default size, 50000 buses, `psse35.py` setting its own paths.
    - On a 255 MB-stack thread there was no stack overflow, but an endless access-violation loop (38,812 before the 240 s kill).
    - So the stack overflow is a side effect of recursive exception handling. The root is an access violation in `GIC.dll`/`MUSTENG.dll` (35.6.4.0, built 2024-11-05).
    - `python.exe` reserves 1.9 MB of main-thread stack; `psse35.exe` reserves 7.6 MB.
    - The big-stack change drafted earlier was dropped, never committed.
  - `INIT_CHECK.txt` (v3, 10:12) found no wrong DLLs.
    - All 70 modules came from the PSS/E, Python or Windows folders. The Intel runtimes (`libifcoremd` 2024.1, `libiomp5md` 5.0.2023.1212, `libmmd` 20.0) came from PSSBIN; `MSVCP140`/`VCOMP140` 14.50 came from System32; `VCRUNTIME140`/`_1` 14.38 came from Python's folder.
    - `SetDllDirectory(PSSBIN)`, pre-loading PSSBIN's runtimes, and a `python.exe` copy with `VCRUNTIME140` 14.50 all crashed the same way.
    - The machine: Windows 10.0.26200 (Windows 11), Intel Family 6 Model 198 with 24 logical CPUs. The environment has `IFORT_COMPILER15`, `INTEL_DEV_REDIST` and `INTEL_LICENSE_FILE` (Intel Composer XE 2015) and `PYTHONSTARTUP` (VS Code).
    - v4 was sent next.
  - The user asked whether the PSS/E 34.8 DLLs can be used with 35. No: they are 32-bit and PSS/E 35 is 64-bit. `dsusr.dll` is rebuilt by the build; vendor DLLs need their PSS/E 35 builds. v34 (`z7_con`) keeps working with them.
  - Also from the check:
    - All 84 `.dll` in each of `Base\` and `Projects\` are 32-bit PSS/E 34 builds: Vestas, SMA, ABB HVDC, PE, GE, `MyUsrdll.dll`, `dsusr.dll`, and more. Each model the deck uses needs its PSS/E 35 64-bit build.
    - The folders have `MyCompile34.bat` and `MyCload4.bat`, which point at PSSE34 and `PSSE33.lib`. The panel's `DYR_COMPILE_BATS` names `MyCompile35.bat` and `MyCload41.bat`, which do not exist yet.
  - The study sets `SetErrorMode(SEM_NOGPFAULTERRORBOX)`, so its own crashes may not reach the Application event log. The check script's children do not set it.
- **Not yet done:**
  - A full e2e run of the renamed `_v35` set. The user cancelled it twice.
  - A run on real PSS/E 35 that gets past `psseinit`.

## User setup steps for v35 (on their machine)
1. Install 64-bit Python 3.9 at `C:\Python39`. The scripts compile on 3.11 and use only the standard library.
2. Create `C:\KV\ENGIE\CQ_MIT_35`:
   - Put the scripts in the layout above. `CQ_MIT_35_scripts.zip`, already sent, has that layout.
   - Copy the `.sav`, `.dyr` and `.idv` files and `FAULT_LISTS_BPM\`.
   - Do **not** copy the results folders, comparisons, `.snp`/`.cnv`/`.cnl` files, `dsusr.dll` or vendor `.dll` files.
3. In `Base\` and `Projects\`:
   - Create `MyCompile35.bat` from `MyCompile34.bat`, and update `MyCload41.bat`, changing the PSSE34 paths to `PSSE35\35.6`.
   - Rebuild `dsusr.dll` as 64-bit.
   - Get 64-bit PSS/E 35 versions of the vendor model DLLs. The scripts `addmodellibrary` every `*.dll` in the study folder.
4. For the first launch, set `FRESH_START = True` and `FORCE_REBUILD = True` (then back to False), and `ONLY_FAULTS = ["F01-F03"]` with one project. Run `C:\Python39\python.exe z7_main_v35.py`.
5. Expect `[init] PSS/E: C:\Program Files\PTI\PSSE35\35.6\PSSPY39` in the log. The CodeMeter licence must cover PSS/E 35.

## Known risks / things to check on first real v35 run
- **psspy APIs** used with fallbacks (`branch_chng_3`, `two_winding_chng_6/_5`, `three_wnd_imped_chng_4/_3`, `machine_chng_2`, `switched_shunt_chng_3`, `dist_bus_fault_2`, `ascc_currents`) are assumed to still exist in 35. Run `z7_probe_psse_v35.py` to check.
- **Channel file type:** the scripts write and read `.out` files. Confirm that PSS/E 35 writes `.out` (not `.outx`) for the names given, and that `dyntools.CHNF` reads them.
- **Comments and messages about 32-bit memory** are unchanged. They're harmless under 64-bit Python.

## Cosmetic, seen in the first real run (not fixed)
- With `RUN_CASES = "base"`, `z7_main` still prints "running BOTH studies at once -- 4 + 4 = 8 concurrent PSS/E session(s)" and "the other case is still running". `PROJ` shows rc=2 (not run).

## Standing user constraints (apply in the new thread)
- Standard library only.
- v34 must stay Python 3.4-compatible. v35 targets Python 3.9 (3.7–3.11).
- Keep the user's panel settings exactly as in their last upload.
- Test edge cases end to end and check that other scripts are not affected.
- Concise replies.
- Write "nodes", not "hops".
- No model identifiers in commits.
- Send full scripts via SendUserFile.
- Commit to `claude/great-ramanujan-c0v50u`, with no PR unless asked and no untracked files.
