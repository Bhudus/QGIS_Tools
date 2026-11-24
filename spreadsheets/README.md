# Drainage spreadsheets

Excel workbooks (with macros) for subsurface drainage design, by Richard Cooke. Each
folder has the workbook, a zip of it for downloading, a README and the workbook's VBA
code as text (`vba/`) so you can read the macros before running them.

| Workbook | What it does |
|---|---|
| [DRAINMOD Calibration](drainmod_calibration/) | Calibrates a DRAINMOD project to measured drain flow and water table depth by maximizing the KGE (DDS search, runs side by side), after a sensitivity analysis that ranks the parameters |
| [DRAINMOD Drainage Design](drainmod_drainage_design/) | Runs a DRAINMOD project for every combination of drain depth, drain spacing and weather set (current and future), and charts relative yield against spacing; for a chosen depth and spacing, gives the probability of exceedance of daily drain flow and monthly design flows under both weather sets |
| [DRAINMOD Path Fixer](drainmod_path_fixer/) | Rewrites the paths in a moved or copied DRAINMOD project (.prj, .gen and input files) so DRAINMOD finds its files, with a check first, .bak copies and undo |
| [Illini Drainage Design Tables and Calculators](illini_drainage_design_tables/) | Ditch radii, drain lengths and grades, wheel loads, trench depths, pumping plants and Manning's equation for open channels, converted from the Illinois AGENG Visual Basic programs |
| [Illinois Drain Spacing Calculator](illinois_drain_spacing/) | Looks up an Illinois soil's properties and gives the drain spacing from the Hooghoudt or Kirkham (steady state), van Schilfgaarde (transient) or NRCS subirrigation equation |
| [Main Telescoping Worksheet](main_telescoping_worksheet/) | Sizes the sections of a telescoping main from the laterals, upstream area, attached pipes, inflows and inlets it carries |
| [Pipe Sizing](pipe_sizing/) | Manning's equation for drain pipe with optional sediment: drained area, standard tile size, slope, flow rate or velocity from any two of the others, with a pipe capacity chart |
| [Tile Outlet Flows](tile_outlet_flows/) | Gives the flow from a tile system for each of a column of stream depths, from the rating curve of the Tile Outlet Submergence plugin, with a chart of stream depth against flow |
| [Tile Slopes](tile_slopes/) | Sets tile depths and grades along a main or lateral from a surface profile, within depth and slope limits |

## Opening a downloaded workbook

Windows marks files downloaded from the internet, and Excel then blocks their macros.
Before opening, right-click the zip (or the .xlsm) → **Properties** → tick **Unblock** →
**OK**. Unblocking the zip before extracting it also unblocks the workbook inside. Then
open the workbook and click **Enable Content** if Excel asks.

The `vba/` files are copies of the code for reading; the workbook already contains it.
