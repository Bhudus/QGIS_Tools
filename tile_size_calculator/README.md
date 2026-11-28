# Tile Size Calculator (QGIS plugin)

Sets the drainage coefficients of an
[Illini Subsurface Drain Layout](../illini_subsurface_drain_layout/) network and
resizes its pipes. Choose one coefficient for every line, one per Strahler
order, or click pipes on the map and give each its own; choose the pipe type
for the whole network or section by section (for example smooth wall on part
of the main); the panel shows at once how the pipe sizes change, and **Apply
to layer…** writes the new sizes. The **Bill of materials** tab turns the
network into a priced bill (pipe by size, tees, wyes, reducers, end caps,
outlet pipe, rodent guard, structures and installation) from a price table you
edit, and makes a print layout for a quote or an EQIP package.

Requires QGIS 3.34 LTR or newer. Uses only QGIS.

## Install

1. Download [`tile_size_calculator.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/tile_size_calculator/tile_size_calculator.zip).
2. In QGIS: **Plugins ▸ Manage and Install Plugins… ▸ Install from ZIP**, choose
   the file and click **Install Plugin**.
3. Open it with the toolbar button, **Plugins ▸ Tile Size Calculator** or
   **Vector ▸ Tile Drainage ▸ Tile Size Calculator**. The panel opens at the
   bottom of the window.

## Use

The panel works on the **Drainage network** layer of Illini Subsurface Drain
Layout, run with *Sizing: size the pipes* on (the default). It finds that layer
in the project by itself; choose another under **Network**.

1. **Drain spacing** and **Pipe**: the drain spacing comes from the layer's
   DRAIN_SPACE field (or from D_AREA and FLOW_LENGTH in older layers). Pipe is
   the pipe type (material, for Manning's n): single wall (n by size), smooth
   wall or clay or concrete. **As in the layer** (the default) keeps each
   line's own type, from its PIPE_TYPE field (single wall where there is
   none); choosing a type sets it for every line except sections set on their
   own (below).
2. Choose how the drainage coefficient is set:
   - **One coefficient**: the value in the box, for every line.
   - **By Strahler order**: a box for each order in the network (TILE_ORDER).
     For example 0.375 in/day for order 1 and 0.5 above.
   - **Pipe by pipe**: press **Pick a pipe** and click a pipe on the map (a
     lateral, submain or main: all its lines). The line beside the button
     names it, with its orders and sizes. Type its coefficient and press
     **Set for this pipe**. Repeat for as many pipes as you like: they are
     listed on the right and drawn orange on the map. Every other line keeps
     the coefficient it already has in the layer, so you can apply by order
     first and then change single pipes. **Clear pipe settings** forgets
     them; select one in the list and press Delete to forget that one.
3. **Pipe type section by section.** Whatever the coefficient option, **Pick a
   pipe** and click a pipe, typically the main: the middle table lists its
   sections (its lines between junctions) from the downstream end up, with
   their stations from that end, Strahler order, coefficient and size (now →
   after). Choose a pipe type for any section in the last column; the sizes
   update at once. Click a row to show that section in red on the map.
   **Clear settings** forgets the section types and pipe coefficients set
   here.
   No pipe is left smaller than a pipe draining into it (when the layer has
   from_node and to_node): a steep link that would size smaller than the pipe
   above it takes that pipe's size, with the note "sized up to N in to match
   the pipe above" in `flag`.
4. The table on the left shows the length of pipe of each nominal size now
   and after, and the line under it how many lines change size (larger or
   smaller) or coefficient, and any that need more than 42 in.
5. **Apply to layer…** writes D_COEFF, PIPE_TYPE, CUM_SEG_FLOW, ACTUAL_SIZE,
   NOMINAL, D_AREA and DRAIN_SPACE for the lines that change (adding the
   PIPE_TYPE field to layers made before Illini Subsurface Drain Layout 0.18), and adds or removes the
   "needs more than 42 in pipe" note in `flag`. It is one edit, and the layer
   is left in edit mode: save the edits to keep them, undo them, or roll them
   back. **Reload layer** reads the layer again after edits made elsewhere.

The coefficient, the pipe type, the option and the per-order values are
remembered between sessions; pipe coefficients and section types are kept in
the layer once applied.

## Bill of materials and cost estimate

The **Bill of materials** tab lists what the network needs, with the pipe sizes
shown on the Pipe sizes tab (applied or not), and prices it.

**Quantities** come from the network layer:

| Item | How it is counted |
|---|---|
| Pipe | Length of each pipe type and nominal size, plus the **Waste** allowance. |
| Tee, wye | One where each pipe joins another, sized run × branch (run: the pipe downstream of the junction). A pipe joining at up to 67.5° to the run is a wye, otherwise a tee. |
| Reducer | Where the run changes size at a junction or along a pipe, sized large × small. |
| Elbow | Where a pipe starts at a junction and the pipe carried on into it turns more than 22.5°. |
| End cap | At the upper end of every pipe. |
| Outlet pipe (rigid) | **Outlet pipe** length (default 20 ft; 0 for none) of the outlet size. |
| Rodent guard | One of the outlet size, if ticked. |
| Installation | Length of tile of each size, if **Installation** is ticked, priced per length. |
| Structures, mobilization | The numbers you enter: inspection ports, water control structures, surface inlets, mobilization. **From map** counts the water control structures from a *Control structures* layer in the project (made by [Illini Subirrigation Layout](../illini_subirrigation_layout/) or [Water Management Zones](../water_management_zones/)), taking only those on this network and counting a structure in both layers once. |

Lengths are in the units of the layer's length field (**Lengths in**: ft or m).

**Prices** come from the price table on the right, which you edit like a
spreadsheet; it is kept between sessions. Each row prices every item of its
category and item with that size; a fitting without a row of its own size takes
the row of its run size (a 6 × 4 in tee takes "Tee, 6 in"), and size `*` covers
every size. Rows the bill needs are added with no price, and the bill shows
"no price" until you fill them in. The table starts empty of prices: enter your
own, or **Import prices…** from a CSV file with the columns category, item,
size, unit, price and (optionally) practice; **Export prices…** writes one to
edit or share. **Practice** is the NRCS conservation practice code: 606
Subsurface Drain for pipe, fittings, outlet and installation and 587 Structure
for Water Control for water control structures by default; change any of them.

Below the tables: the subtotal, **Markup** and **Tax** (on the subtotal plus
markup), the total, and the total of each practice for an EQIP application.

**Outputs:**

- **Add to map** adds a *Bill of materials* table layer (the priced lines and
  the totals) and a *Fittings* point layer (tees, wyes, reducers, elbows, end
  caps and the outlet, labelled) in a *Tile Size Calculator* group, replacing
  the ones made before.
- **Save bill…** writes the bill as a CSV file, with the project details at the
  top.
- **Print layout…** adds the layers and makes a print layout, opened in the
  layout designer to adjust and export to PDF:
  - With no template, a ready-made quote on US Letter: page 1, a landscape
    plan of the network and fittings with legend, scale bar, north arrow,
    project details and the total; page 2, the bill of materials and cost
    estimate (more pages if the table runs on).
  - With your own template (.qpt, chosen beside the button), the layout is
    made by the [Layout from Template](../layout_from_template/) plugin when
    it is installed (it fits the map to the network and sets the title), else
    loaded directly. An attribute table whose frame has the item ID
    `bom_table` is filled with the bill, and labels can show the project
    details and totals with `[% @tsc_project %]`, `@tsc_client`,
    `@tsc_prepared_by`, `@tsc_date`, `@tsc_notes`, `@tsc_subtotal`,
    `@tsc_markup`, `@tsc_tax` and `@tsc_total`. An easy way to make one: print
    the built-in quote, change it in the layout designer (logo, colours, terms
    and conditions, an EQIP cover page), and save it with **Layout ▸ Save as
    Template…**. Run it from **Print layout…** so the table is filled in.

The project details (project or field, client, prepared by, notes) and all the
settings are remembered between sessions. The quote is a planning estimate:
it counts what the network layer shows, so check it against the plan before
it goes out, and it is not an NRCS form.

## How the sizes are worked out

As in Routine K of the Illini Drainage Tools, and in Illini Subsurface Drain
Layout: each line of the network is sized for the flow from all the tile
upstream of it,

flow (cfs) = FLOW_LENGTH (ft) × drain spacing (ft) × D_COEFF (in/day) / 12 / 86,400,

at its SIZE_SLOPE, with Manning's equation for a full pipe. The nominal size is
the smallest listed size (4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 30, 36, 42 in)
larger than the computed diameter. A line is sized with its own coefficient,
the one the option gives it: in **By Strahler order**, the coefficient of the
line's order applies to all the tile draining through it, which is how
Routine K uses per-order coefficients. Single-wall pipe uses n = 0.015 up to
8 in, 0.017 to 12 in and 0.020 above; smooth wall 0.011; clay or concrete
0.013.

Only the coefficient changes: the tile order, the tile length upstream and the
slope come from the layer. After editing the tile in Tile Profile, run Illini
Subsurface Drain Layout again to update SIZE_SLOPE.

## Credits and licence

By Richard Cooke and Anamelechi Falasy. The sizing is a port of Routine K
(Network Pipe Sizing) of the Illini Drainage Tools (A. Falasy and R. Cooke,
University of Illinois Urbana-Champaign). GPL-3.0-or-later; see LICENSE.
