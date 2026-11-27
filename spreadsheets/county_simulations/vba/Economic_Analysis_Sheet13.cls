Attribute VB_Name = "Sheet13"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Attribute VB_Control = "CheckBox1, 26, 0, MSForms, CheckBox"
Attribute VB_Control = "CommandButton2, 25, 1, MSForms, CommandButton"
Attribute VB_Control = "cmdSort, 24, 2, MSForms, CommandButton"
Attribute VB_Control = "ExtraCostQ, 23, 3, MSForms, CommandButton"
Attribute VB_Control = "AddedCostQ, 22, 4, MSForms, CommandButton"
Attribute VB_Control = "LatCostQ, 21, 5, MSForms, CommandButton"
Attribute VB_Control = "DepPremQ, 20, 6, MSForms, CommandButton"
Attribute VB_Control = "CritDepQ, 19, 7, MSForms, CommandButton"
Attribute VB_Control = "SoyPriceQ, 18, 8, MSForms, CommandButton"
Attribute VB_Control = "MaxSoyQ, 17, 9, MSForms, CommandButton"
Attribute VB_Control = "CornPriceQ, 16, 10, MSForms, CommandButton"
Attribute VB_Control = "MaxCornQ, 15, 11, MSForms, CommandButton"
Attribute VB_Control = "MarginalQuery, 14, 12, MSForms, CommandButton"
Attribute VB_Control = "InterestQuery, 13, 13, MSForms, CommandButton"
Attribute VB_Control = "AddedCost, 12, 14, MSForms, TextBox"
Attribute VB_Control = "MainFitting, 11, 15, MSForms, TextBox"
Attribute VB_Control = "LateralCost, 10, 16, MSForms, TextBox"
Attribute VB_Control = "DepthTile, 9, 17, MSForms, TextBox"
Attribute VB_Control = "CriticalDepth, 8, 18, MSForms, TextBox"
Attribute VB_Control = "SoyPrice, 7, 19, MSForms, TextBox"
Attribute VB_Control = "MaxSoyYield, 6, 20, MSForms, TextBox"
Attribute VB_Control = "CornPrice, 5, 21, MSForms, TextBox"
Attribute VB_Control = "MaxCornYield, 4, 22, MSForms, TextBox"
Attribute VB_Control = "Life, 3, 23, MSForms, TextBox"
Attribute VB_Control = "TaxRate, 2, 24, MSForms, TextBox"
Attribute VB_Control = "InterestRate, 1, 25, MSForms, TextBox"
Private Sub AddedCost_Change()
    AddedCost.Font.Size = 10
End Sub

Private Sub AddedCostQ_Click()
a$ = "The cost of the main and fittings in the tile drainage system is expressed as a percent of the lateral cost per foot."
MsgBox (a$)
End Sub

Private Sub CheckBox1_Click()
If CheckBox1.Value = False Then
    Worksheets("Economic Analysis").Range("CDFlag") = 0
    Worksheets("Economic Analysis").Columns("N:N").EntireColumn.Hidden = False
    Worksheets("Economic Analysis").Columns("O:O").EntireColumn.Hidden = True
 Else
    Worksheets("Economic Analysis").Range("CDFlag") = 1
    Worksheets("Economic Analysis").Columns("N:N").EntireColumn.Hidden = True
    Worksheets("Economic Analysis").Columns("O:O").EntireColumn.Hidden = False
 End If
End Sub

Private Sub CommandButton1_Click()
Dim FullRange As Range
'Worksheets("Economic Analysis").Shapes("Arrow").Visible = True
Worksheets("Economic Analysis").Columns("T:AC").EntireColumn.Hidden = False
Range("DataRange").Copy
Range("T2").PasteSpecial xlPasteValues
Range("T2").PasteSpecial xlPasteFormats

Range("DataRangeSort").Sort key1:=Range("ab3"), Order1:=xlDescending, Header:=xlNo
Set FullRange = Range("DataRangeSort")

ist = 1

While FullRange(ist, 1) = 0
 ist = ist + 1
Wend

j2 = WorksheetFunction.Min(10, Worksheets("Economic Analysis").Range("NPTS"))

For ir = 1 To j2: For ic = 1 To 4
    Worksheets("Report").Cells(20 + ir, 15 + ic) = FullRange(ist - 1 + ir, ic)
    Next ic
    Worksheets("Report").Cells(20 + ir, 20) = FullRange(ist - 1 + ir, 9)
    Worksheets("Report").Cells(20 + ir, 21) = FullRange(ist - 1 + ir, 10)
Next ir

Range("DataRangeSort").Sort key1:=Range("ab4"), Order1:=xlAscending, Header:=xlNo
    Worksheets("Economic Analysis").Columns("X:X").EntireColumn.Hidden = False



If CheckBox1.Value = False Then
    Worksheets("Economic Analysis").Columns("Z:Z").EntireColumn.Hidden = False
    Worksheets("Economic Analysis").Columns("AA:AA").EntireColumn.Hidden = True
    Worksheets("Economic Analysis").Columns("Y:Y").EntireColumn.Hidden = True
 Else
    Worksheets("Economic Analysis").Columns("Z:Z").EntireColumn.Hidden = True
    Worksheets("Economic Analysis").Columns("AA:AA").EntireColumn.Hidden = False
    Worksheets("Economic Analysis").Columns("Y:Y").EntireColumn.Hidden = False
 End If

End Sub

Private Sub cmdSort_Click()

End Sub

Private Sub CommandButton2_Click()


'Worksheets("Economic Analysis").Shapes("Arrow").Visible = False
Worksheets("Economic Analysis").Columns("T:AC").EntireColumn.Hidden = True
Worksheets("Economic Analysis").Visible = False
Worksheets("Main").Activate
End Sub

Private Sub CornPrice_Change()
    CornPrice.Font.Size = 10
End Sub

Private Sub CornPriceQ_Click()
a1$ = " Total costs per bushel of corn range from $4.84 in central Illinois to $6.79 in southern Illinois."
a2$ = " In 2017, the price per unit of corn was $3.35/bu."
a$ = a1$ & a2$
MsgBox (a$)
End Sub

Private Sub CritDepQ_Click()
a1$ = "Agricultural machinery are designed to install tile drains at a depth of 6 to 7 ft. "
a2$ = "This is referred to as the critical depth. A depth of installation greater than the critical depth "
a3$ = "has an associated premium cost for the adjustment of the machine."
a$ = a1$ & a2$ & a3$
MsgBox (a$)
End Sub

Private Sub CriticalDepth_Change()
    CriticalDepth.Font.Size = 10
End Sub

Private Sub DepthPremium_Change()
    DepthPremium.Font.Size = 10
End Sub

Private Sub DepPremQ_Click()
a$ = "The depth premium is the additional cost per foot for installing tile drains at a depth greater than the critical depth."
MsgBox (a$)
End Sub

Private Sub DepthTile_Change()
    DepthTile.Font.Size = 10
End Sub


Private Sub ExtraCostQ_Click()
a$ = "This may include extra costs such as operating cost for machinery, hauling and maintenance of tile drains or addressing environmental impacts of agricultural drainage. "
MsgBox (a$)
End Sub

Private Sub InterestQuery_Click()
a$ = "Many tools have been developed to assist in the determination of the practicability of drainage. This Guide, for example, includes an economic calculator that can be used to determine the profitability of a drainage system. "
'It provides many measures of profitability, but they are all consistent with each other, and are but a reflection of user preference."
MsgBox (a$)
End Sub

Private Sub InterestRate_Change()
    InterestRate.Font.Size = 10
End Sub

Private Sub LatCostQ_Click()
a1$ = "As a general guideline, the cost of tile drainage is typically about $1.00 per foot of tile. "
a2$ = "Spacing between lateral drains is a major factor affecting the total cost of the drainage system. "
a$ = a1$ & a2$
MsgBox (a$)
End Sub

Private Sub LateralCost_Change()
    LateralCost.Font.Size = 10
End Sub

Private Sub Life_Change()
    Life.Font.Size = 10
End Sub

Private Sub MainFitting_Change()
    MainFitting.Font.Size = 10
End Sub

Private Sub MarginalQuery_Click()
a1$ = "Marginal tax rate is the amount of tax paid on an additional dollar of income. "
a4$ = "The marginal tax rate increases as the income rises."
a$ = a1$ & a4$
MsgBox (a$)
End Sub

Private Sub MaxCornQ_Click()
a1$ = "According to USDA, the corn yield in Illinois was 197 bu/ac in 2016, and 201 bu/ac in 2017. "
a2$ = "Illinois has an average corn yield of 200 bu/ac as stated by the U.S. Dept. of Agriculture's National Agricultural Statistics Services"
a$ = a1$ & a2$
MsgBox (a$)
End Sub

Private Sub MaxCornYield_Change()
    MaxCornYield.Font.Size = 10
End Sub

Private Sub MaxSoyQ_Click()
a1$ = "According to USDA, the soybean yield in Illinois was 59 bu/ac in 2016, and 58 bu/ac in 2017. "
a2$ = "Illinois has an average soybean yield of 50 bu/ac as stated by the U.S. Dept. of Agriculture's National Agricultural Statistics Services."
a$ = a1$ & a2$
MsgBox (a$)
End Sub

Private Sub MaxSoyYield_Change()
    MaxSoyYield.Font.Size = 10
End Sub


Private Sub SoyPrice_Change()
    SoyPrice.Font.Size = 10
End Sub

Private Sub SoyPriceQ_Click()
a1$ = " Total costs per bushel of soybean range from $11.05 in central Illinois to $12.31 in southern Illinois."
a2$ = " In 2017, the price per unit of soybean was $9.6/bu."
a$ = a1$ & a2$
MsgBox (a$)
End Sub

Private Sub TaxRate_Change()
    TaxRate.Font.Size = 10
End Sub
