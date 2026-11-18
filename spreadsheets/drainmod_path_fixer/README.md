# DRAINMOD Path Fixer

**Workbook:** `DRAINMOD_PathFixer.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/drainmod_path_fixer/drainmod_path_fixer.zip)

DRAINMOD writes full paths into a project's .prj and .gen and into some of its input
files (.cin crop files, .dmn, .dmp, .dms, .dmf, .sin). A project folder that is copied or
moved, for example from `C:\DDESIGN` to `C:\Users\you\Downloads\DDESIGN`, still points to
the old place, and DRAINMOD then cannot find its files. This workbook points a moved
project at where it is now, and finds two other problems that stop DRAINMOD or make it
quietly leave a crop out.

## Needs

* Windows Excel with macros enabled.
* One or more DRAINMOD projects (.prj with its .gen). DRAINMOD itself is not needed.

## Use

On the **Fix Paths** sheet:

1. Select a project (.prj), or a folder to look at all its projects. Run copies such as
   `name_cal.prj` and `name_dsg.prj` from the calibration and drainage design workbooks are
   skipped. Or select a .prj and set **Look at every .prj in that folder** to Yes.
2. Click **1. Check paths**. Nothing is changed. The old place is the folder the project
   names for its .gen; the new place is where the .prj is now. Common folder names at the
   end are kept, so `C:\DDESIGN\inputs\` → `E:\Work\DDESIGN\inputs\` moves everything under
   `C:\DDESIGN\` to `E:\Work\DDESIGN\`; paths outside the project folder are left alone.
   * **Changes** lists every line that would change, before and after.
   * **Files** lists every file looked at, the project that names it, and its old and new
     place.
   * **Problems** lists:
     * **Missing**: files the project names that are not there even after the fix.
     * **Too long**: crop file paths longer than DRAINMOD reads (48 characters). DRAINMOD
       cuts such a path short and silently leaves that crop out, so move the project
       folder to a shorter path, for example directly under `C:\`.
     * **Crop count**: the .prj names a different number of crops than its phosphorus file
       (.dmp) lists in `*Crops`, so DRAINMOD will not run the project.
3. Click **2. Fix paths**. Each file that changes is saved first as `<file>.bak` (an
   existing .bak is kept, so it stays the original), then rewritten. A project with a crop
   count problem is made corn only: the .prj keeps just its corn crop (as crop 1, with its
   `CROPNAME` and `[CropAlias]` lines) and the .dmp just the corn row (as `C1`). The corn
   crop is the one whose file name contains "corn" (crop 1 if none does).
4. **3. Undo** restores the .bak copies of the selected projects' files and removes them.

If the old or new place is not found correctly (for example when folders were renamed),
type both on the **Fix Paths** sheet; they are then used for every selected project.

The [DRAINMOD Drainage Design](../drainmod_drainage_design/) workbook fixes the paths of
its own runs by itself; this workbook is for using the projects in DRAINMOD.

## Code

`vba/modPaths.bas` (all the macros) and `vba/ThisWorkbook.cls` (adds the buttons when the
workbook opens).

## Versions

* **1** – First published version.
