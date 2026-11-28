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
' ticked: the analysis uses the managed drainage (DWM or subirrigation) results
If CheckBox1.Value = True Then
    Range("CDFlag").Value = 1
Else
    Range("CDFlag").Value = 0
End If
BuildNPVChart
End Sub

Private Sub cmdSort_Click()
' shows or hides the alternatives listed from best to worst net present value
Dim showIt As Boolean
showIt = Me.Columns("V").Hidden
Me.Columns("V:AE").Hidden = Not showIt
If showIt Then Application.Goto Me.Range("V1"), True
End Sub

Private Sub CommandButton2_Click()
Me.Columns("V:AE").Hidden = True
Worksheets("Economic Analysis").Visible = False
Worksheets("Main").Activate
End Sub

Private Sub Worksheet_Activate()
On Error Resume Next
cmdSort.Caption = "Ranked list"
CommandButton2.Caption = "Back to Main"
If WMCode() = 3 Then CheckBox1.Caption = "Subirrigation" Else CheckBox1.Caption = "DWM"
On Error GoTo 0
BuildNPVChart
End Sub

' Chart of net present value against drain spacing, one line per drain depth
' (from the table in columns H to T). Rebuilt each time the sheet is shown.
Public Sub BuildNPVChart()
Dim co As ChartObject, cht As Chart, s As Series
Dim r As Long, r0 As Long, d As Variant, nSer As Integer
On Error Resume Next
Set co = Me.ChartObjects("NPVChart")
On Error GoTo Failed
If co Is Nothing Then
    Set co = Me.ChartObjects.Add(Me.Range("B38").Left, Me.Range("B38").Top, Me.Range("B38:G38").Width, 270)
    co.Name = "NPVChart"
End If
Set cht = co.Chart
cht.ChartType = xlXYScatterLines
Do While cht.SeriesCollection.Count > 0
    cht.SeriesCollection(1).Delete
Loop
r = 4
Do While r <= 102
    If VarType(Me.Cells(r, "O").Value) = vbDouble Then
        d = Me.Cells(r, "H").Value
        r0 = r
        Do While r < 102
            If CStr(Me.Cells(r + 1, "H").Value) <> CStr(d) Or VarType(Me.Cells(r + 1, "O").Value) <> vbDouble Then Exit Do
            r = r + 1
        Loop
        Set s = cht.SeriesCollection.NewSeries
        s.Name = Format(d, "0.0") & " ft deep"
        s.XValues = Me.Range(Me.Cells(r0, "I"), Me.Cells(r, "I"))
        s.Values = Me.Range(Me.Cells(r0, "O"), Me.Cells(r, "O"))
        nSer = nSer + 1
    End If
    r = r + 1
Loop
co.Visible = (nSer > 0)
If nSer = 0 Then Exit Sub
cht.HasTitle = True
cht.ChartTitle.Text = "Net present value ($/acre)"
cht.HasLegend = True
cht.Legend.Position = xlLegendPositionBottom
With cht.Axes(xlCategory, xlPrimary)
    .HasTitle = True
    .AxisTitle.Text = "Drain spacing (ft)"
End With
With cht.Axes(xlValue, xlPrimary)
    .HasTitle = True
    .AxisTitle.Text = "NPV ($/acre)"
End With
Exit Sub
Failed:
End Sub

Private Sub CornPrice_Change()
    CornPrice.Font.Size = 10
End Sub

Private Sub CornPriceQ_Click()
a$ = "In 2025, the total cost of growing corn in Illinois was about $4.98/bu in northern Illinois, " & _
     "$5.00 to $5.03/bu in central Illinois and $6.50/bu in southern Illinois (farmdoc). " & _
     "Illinois farm records valued the 2025 corn crop at $4.20/bu at the end of 2025."
MsgBox a$, vbInformation
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
a$ = "One-time costs per acre added to the cost of the drainage system, such as surveying, outlet work or hauling. " & _
     "Costs or savings that come every year go in 'Annual added costs' and 'Annual savings' below."
MsgBox (a$)
End Sub

Private Sub InterestQuery_Click()
a$ = "The interest (discount) rate used to discount the yearly after-tax cash flows for the net present value, " & _
     "the discounted payback and the break-even income."
MsgBox (a$)
End Sub

Private Sub InterestRate_Change()
    InterestRate.Font.Size = 10
End Sub

Private Sub LatCostQ_Click()
a$ = "Installed 4-inch lateral tubing costs about $1.10 to $1.30 per foot (NC State Extension, revised 2025: " & _
     "$1.07/ft without a fabric filter, $1.28/ft with one). Costs vary with location, contractor and year, " & _
     "so use a local quote if you have one. The spacing between laterals is a major factor in the total cost."
MsgBox a$, vbInformation
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
a$ = "The Illinois average corn yield was a record 214 bu/acre in 2025 (USDA NASS). " & _
     "On Illinois farms in the Farm Business Farm Management records, 2025 corn yields averaged " & _
     "239 bu/acre in northern Illinois, 242 and 234 bu/acre in central Illinois (high- and low-rated soils) " & _
     "and 172 bu/acre in southern Illinois."
MsgBox a$, vbInformation
End Sub

Private Sub MaxCornYield_Change()
    MaxCornYield.Font.Size = 10
End Sub

Private Sub MaxSoyQ_Click()
a$ = "The Illinois average soybean yield was a record 62.5 bu/acre in 2025 (USDA NASS). " & _
     "On Illinois farms in the Farm Business Farm Management records, 2025 soybean yields averaged " & _
     "71 bu/acre in northern Illinois, 75 and 71 bu/acre in central Illinois (high- and low-rated soils) " & _
     "and 51 bu/acre in southern Illinois."
MsgBox a$, vbInformation
End Sub

Private Sub MaxSoyYield_Change()
    MaxSoyYield.Font.Size = 10
End Sub


Private Sub SoyPrice_Change()
    SoyPrice.Font.Size = 10
End Sub

Private Sub SoyPriceQ_Click()
a$ = "In 2025, the total cost of growing soybeans in Illinois was about $12.31/bu in northern Illinois, " & _
     "$12.07 to $12.13/bu in central Illinois and $16.09/bu in southern Illinois (farmdoc). " & _
     "Illinois farm records valued the 2025 soybean crop at $10.20/bu at the end of 2025."
MsgBox a$, vbInformation
End Sub

Private Sub TaxRate_Change()
    TaxRate.Font.Size = 10
End Sub
