# Economic Analysis

**Workbook:** `Economic_Analysis.xlsm` · Version 1 ·
[download zip](https://github.com/Bhudus/QGIS_Tools/raw/main/spreadsheets/economic_analysis/economic_analysis.zip)

After-tax economic analysis of a drainage system, per acre, converted from the Visual Basic
program Econ_Analysis. From the cost of the system, the yield increases and crop prices, it gives
the net increase in income, the net present value, the undiscounted and discounted years to
payback, the internal rate of return and the income needed to break even. Everything is worksheet
formulas, so the results update as you type; the workbook has no macros.

| Sheet | What it does |
|---|---|
| **Analysis** | The inputs and results, with a chart of cumulative after-tax cash flow (payback is where each line crosses $0). An **Input Set** dropdown runs the model on your own inputs or on one of the scenarios |
| **Scenarios** | Pessimistic, Expected and Optimistic input sets side by side, each with all six results |
| **Sensitivity** | Net present value for a range of crop 1 and crop 2 prices, and for a range of interest rates and drainage costs, centred on the current case |
| **Drainage Cost** | Cost per acre by average lateral costs (area, tile spacing, lateral cost, mains, other costs) or by detailed pipe costs (length of each pipe size from 3 to 36 in) |
| **Cash Flow** | Year-by-year depreciation, taxable income, tax, after-tax cash flow and discounted cash flow for the main model |
| **Scenario Cash Flow** | The same for each scenario |

## Use

Yellow cells with blue text are inputs; green-filled cells are results.

1. On **Drainage Cost**, pick the costing method and fill in its inputs. Or skip it and type the
   cost per acre into **Cost override** on **Analysis**.
2. On **Analysis**, enter the depreciation schedule, interest rate, marginal tax rate and life
   of the system, then the yield increases and prices for crops 1 and 2 and any additional costs
   and savings.
3. Read the results. To compare cases, fill in **Scenarios** and pick one in **Input Set**.

The Pessimistic and Optimistic scenario values are placeholders; replace them with your own.
Expected and the Analysis inputs start at the original program's defaults (10-year depreciation,
6% interest, 28% tax, 50-year life, 15 bu at $2.25 and 5 bu at $6.25, $0.50 costs, $1.25
savings), which give a net present value of $57.23 per acre for a cost of $400.26 per acre.

## Method

* Net increase in income = (crop 1 yield × price + crop 2 yield × price) / 2 − additional costs
  + additional savings: the two crops are averaged as a rotation.
* Depreciation is straight-line over the schedule with a half-year convention: half in year 1 and
  half in the year after the schedule ends. Tax is the marginal rate × (income − depreciation).
* Net present value discounts the after-tax cash flows at the interest rate, less the cost.
  Payback is the first year the cumulative cash flow (undiscounted or discounted) reaches 0.
* Internal rate of return uses Excel's IRR; as in the original, it is 0 when the simple payback
  (cost / income) is longer than the life of the system.
* Income to break even is the income that makes the net present value 0. The original searched
  for it; the workbook solves it directly, since net present value is linear in income.
* System life and depreciation schedule can be 1 to 100 years.

## Versions

* **1** – First published version. Same results as the original program for the same inputs;
  adds the scenarios, sensitivity tables and cash flow chart.
