# Drainage spreadsheets

Excel workbooks (with macros) for subsurface drainage design, by Richard Cooke. Each
folder has the workbook, a zip of it for downloading, a README and the workbook's VBA
code as text (`vba/`) so you can read the macros before running them.

| Workbook | What it does |
|---|---|
| [DRAINMOD Drainage Design](drainmod_drainage_design/) | Runs a DRAINMOD project for every combination of drain depth, drain spacing and weather set (current and future), and charts relative yield against spacing |
| [Main Telescoping Worksheet](main_telescoping_worksheet/) | Sizes the sections of a telescoping main from the laterals, upstream area, attached pipes, inflows and inlets it carries |
| [Tile Slopes](tile_slopes/) | Sets tile depths and grades along a main or lateral from a surface profile, within depth and slope limits |

## Opening a downloaded workbook

Windows marks files downloaded from the internet, and Excel then blocks their macros.
Before opening, right-click the zip (or the .xlsm) → **Properties** → tick **Unblock** →
**OK**. Unblocking the zip before extracting it also unblocks the workbook inside. Then
open the workbook and click **Enable Content** if Excel asks.

The `vba/` files are copies of the code for reading; the workbook already contains it.
