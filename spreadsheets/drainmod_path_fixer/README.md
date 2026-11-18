# DRAINMOD Path Fixer

**Workbook:** `DRAINMOD_PathFixer.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/drainmod_path_fixer/drainmod_path_fixer.zip)

Points a moved or copied DRAINMOD project at where it is now. DRAINMOD writes full paths
into a project's .prj and .gen and into some input files (.cin crop files, .dmn, .dmp,
.dms, .dmf, .sin). After the project folder is copied or moved (for example from
`C:\DDESIGN` to `C:\Users\you\Downloads\DDESIGN`) it still points to the old place, and
DRAINMOD cannot find its files. This workbook finds the old and new places and rewrites
the paths.

## Use

On the **Fix Paths** sheet:

1. Select a project (.prj), or a folder to fix all its projects. Run copies such as
   `name_cal.prj` and `name_dsg.prj` are skipped. To do every project next to a .prj, set
   *Look at every .prj in that folder* to Yes.
2. Click **1. Check paths**. Nothing is changed yet:
   * **Changes** lists every line that would change, and **Files** every file looked at.
   * **Problems** lists files still missing after the fix, crop file paths longer than
     DRAINMOD reads (48 characters; DRAINMOD silently leaves such a crop out, so move the
     project to a shorter path, e.g. directly under `C:\`), and projects whose .prj and
     phosphorus file (.dmp) list different numbers of crops, which DRAINMOD will not run.
3. Click **2. Fix paths**. Each file that changes is first saved as `<file>.bak` (an
   existing .bak is kept, so it stays the original), then rewritten. A project with
   mismatched crop counts is made corn only: the .prj keeps its corn crop as crop 1 and
   the .dmp its corn row as C1.
4. **3. Undo** puts back every .bak copy of the selected projects' files and removes the
   .bak copies.

## How the old place is found

From the folder the project names for its .gen, compared with where the .prj is now.
Folder names the two share at the end are kept, so `C:\DDESIGN\inputs\` →
`E:\Work\DDESIGN\inputs\` moves everything under `C:\DDESIGN\` to `E:\Work\DDESIGN\`.
Paths outside the project folder are left alone. If the folders were renamed and the old
or new place is not found correctly, type both on the Fix Paths sheet; they are then used
for every selected project.

The [DRAINMOD Drainage Design](../drainmod_drainage_design/) workbook fixes the paths of
its own runs by itself; this workbook is for using projects in DRAINMOD itself.

## Code

`vba/modPaths.bas` (all the macros) and `vba/ThisWorkbook.cls`.

## Versions

* **1** – First published version.
