Attribute VB_Name = "ThisWorkbook"
Attribute VB_Base = "0{00020819-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True

Private Sub Workbook_Open()
    Dim ws As Worksheet, i As Long
    Set ws = Worksheets("Spacing")

    ' Create the Form Controls if missing, then assign their macros
    Sheet9.EnsureControls
    ws.Shapes("CheckBox1").OnAction = "Sheet9.CheckBox1_Click"
    ws.Shapes("CheckBox4").OnAction = "Sheet9.CheckBox4_Click"
    ws.Shapes("CommandButton1").OnAction = "Sheet9.CommandButton1_Click"
    ws.Shapes("CommandButton2").OnAction = "Sheet9.CommandButton2_Click"
    ws.Shapes("CommandButton4").OnAction = "Sheet9.CommandButton4_Click"

    ' Soil list: in-cell drop-down on the Soil Name cell, fed by the Soils table
    Sheet9.EnsureSoilDropdown
    i = 56   ' Drummer
    Sheet9.LoadSoil i

    PictureForm.picSteady.Visible = False
    PictureForm.picTransient.Visible = False
    PictureForm.picSubIrrigate.Visible = False
    PictureForm.picSpacing.Visible = True

    Sheet9.ShowSplash
End Sub

' Close the splash (and cancel its timer) so Excel doesn't reopen the file to run it
Private Sub Workbook_BeforeClose(Cancel As Boolean)
    Sheet9.HideSplash
End Sub
