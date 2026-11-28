Attribute VB_Name = "ThisWorkbook"
Attribute VB_Base = "0{00020819-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Private Sub Workbook_open()

Worksheets("Main").Range("dBaseFlag") = 0
Worksheets("Main").Range("StartFlag") = 0     ' ask for the DRAINMOD folder again in each session
On Error Resume Next
Worksheets("Main").Activate
Worksheets("New Flow File").Visible = xlSheetHidden   ' shown by Change Simulation Parameters
On Error GoTo 0

 
ResetDesignPeriod
SetTimePeriodCaptions
Worksheets("Main").SelectObservedPeriod     ' start with the observed period
On Error Resume Next
Worksheets("New Flow File").CheckBox2.Visible = False   ' shown again when CheckBox1 is ticked
On Error GoTo 0

'Default parameters
FrmIntroduction.StartUpPosition = 3
FrmIntroduction.Show
Application.OnTime Now + TimeSerial(0, 0, 1), "WakeMainSheet"   ' makes the Main sheet's buttons respond at once


End Sub
