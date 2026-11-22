Attribute VB_Name = "ThisWorkbook"
Attribute VB_Base = "0{00020819-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Private Sub Workbook_open()
'Dim gb As Long
Sheets("Plot").Range("ag47") = Sheets("Plot").Range("ag46")
Sheets("Plot").Range("Seddy") = Val(frmMain.txtSediment.Text)
Sheets("Plot").Range("AQ7") = "Sed. Mode"
Sheets("Plot").Range("AR7") = IIf(frmMain.optPer2.Value = True, 2, 1)
Sheet2.EnsureControls
frmMain.optDC3_8.Value = True
frmMain.StartUpPosition = 2

'frmMain.Show
 ' Sheets("Plot").ChartObjects("PicPlott").Visible = True
 Sheets("Plot").ChartObjects("PicPlott").Chart.Refresh:
   'Sheets("Plot").ChartObjects("PicPlott").Visible = False
 'Call frmMain.GetChart
End Sub

Private Sub Wokbook_open()

 Dim cht1 As Chart, ImageName As String, expt As Boolean
  Set cht1 = Sheets("Plot").ChartObjects("PicPlott").Chart
MsgBox ("lets's see")
   ImageName = ThisWorkbook.Path & "\temp1.jpg"
    If FileExists(ImageName) = False Then Kill ImageName
    End
End Sub
