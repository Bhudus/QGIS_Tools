Attribute VB_Name = "Sheet4"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Attribute VB_Control = "OptionButton4, 53, 6, MSForms, OptionButton"
Attribute VB_Control = "CommandButton4, 52, 7, MSForms, CommandButton"
Attribute VB_Control = "Opt_SEW, 30, 8, MSForms, OptionButton"
Attribute VB_Control = "Opt_Work_Days, 29, 9, MSForms, OptionButton"
Attribute VB_Control = "Opt_Dry_Days, 28, 10, MSForms, OptionButton"
Attribute VB_Control = "CommandButton1, 3, 11, MSForms, CommandButton"
Attribute VB_Control = "CommandButton2, 4, 12, MSForms, CommandButton"
Attribute VB_Control = "OptionButton1, 12, 13, MSForms, OptionButton"
Attribute VB_Control = "OptionButton2, 13, 14, MSForms, OptionButton"
Attribute VB_Control = "CommandButton3, 14, 15, MSForms, CommandButton"
Attribute VB_Control = "OptionButton3, 20, 16, MSForms, OptionButton"
Attribute VB_Control = "Opt_Infiltration, 21, 17, MSForms, OptionButton"
Attribute VB_Control = "Opt_Evap, 22, 18, MSForms, OptionButton"
Attribute VB_Control = "Opt_Drained_Vol, 23, 19, MSForms, OptionButton"
Attribute VB_Control = "Opt_Runoff, 24, 20, MSForms, OptionButton"
Attribute VB_Control = "Opt_Overall_Yield, 25, 21, MSForms, OptionButton"
Attribute VB_Control = "Opt_SDI_Excess, 32, 22, MSForms, OptionButton"
Attribute VB_Control = "Opt_Stress_Drought, 33, 23, MSForms, OptionButton"
Attribute VB_Control = "Opt_Drought, 41, 24, MSForms, OptionButton"
Attribute VB_Control = "Opt_Excess, 42, 25, MSForms, OptionButton"
Attribute VB_Control = "Opt_Soy_Overall_Yield, 44, 26, MSForms, OptionButton"
Attribute VB_Control = "Opt_Soy_SDI_excess, 46, 27, MSForms, OptionButton"
Attribute VB_Control = "Opt_Soy_Stress_Drought, 47, 28, MSForms, OptionButton"
Attribute VB_Control = "Opt_Soy_Drought, 49, 29, MSForms, OptionButton"
Attribute VB_Control = "Opt_Soy_Excess, 50, 30, MSForms, OptionButton"
Attribute VB_Control = "CommandButton5, 54, 31, MSForms, CommandButton"

Private Sub CommandButton1_Click()
 
 Dim PltFile As String, NewPath As String
 
 If Worksheets("Main").Range("StartFlag") = 0 Then
    MsgBox ("Select the DRAINMOD folder (it must contain the crops, inputs and outputs folders). You will then be asked for the DRAINMOD program, DMhydro.exe.")
    NewPath = GetFolder(PltFile)
    Worksheets("New Flow File").Range("CurrentPath") = NewPath
    Worksheets("Main").Range("StartFlag") = 1
    DMHydroPath True
  End If
 
 Worksheets("New Flow File").Visible = True
 Worksheets("New Flow File").Activate
  Worksheets("New Flow File").InputState ("Illinois")
  Worksheets("New Flow File").OptionButton1.Value = True
End Sub

Private Sub CommandButton2_Click()
Dim PltFile As String, NewPath As String

 If Worksheets("Main").Range("StartFlag") = 0 Then
    MsgBox ("Select the DRAINMOD folder (it must contain the crops, inputs and outputs folders). You will then be asked for the DRAINMOD program, DMhydro.exe.")
    NewPath = GetFolder(PltFile)
    Worksheets("New Flow File").Range("CurrentPath") = NewPath
    Worksheets("Main").Range("StartFlag") = 1
    DMHydroPath True
 End If
  
'NewPath = GetFolder(PltFile)
'Worksheets("New Flow File").Range("CurrentPath") = NewPath
Worksheets("New Flow File").Visible = True
Worksheets("New Flow File").Activate
  Worksheets("New Flow File").InputState ("Illinois")
  Worksheets("New Flow File").OptionButton1.Value = True
End Sub

Private Sub CommandButton3_Click()
Worksheets("Report").Visible = True
Worksheets("Report").Activate
End Sub


Private Sub Opt_Corn_Yield_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Corn Yield"
plotgraph (Worksheets("DMOutput").Range("CornYld"))
End Sub

Private Sub Opt_Delay_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Delay (%)"
plotgraph (Worksheets("DMOutput").Range("Yield_delay"))
End Sub

Private Sub CommandButton4_Click()
Worksheets("Economic Analysis").Visible = True
Worksheets("Economic Analysis").Activate
If Worksheets("New Flow File").Range("DWMFlag") = 1 Then
  Worksheets("Economic Analysis").CheckBox1.Visible = True
Else
  Worksheets("Economic Analysis").CheckBox1.Visible = False
End If

Worksheets("Economic Analysis").Range("a1").Select
Worksheets("Economic Analysis").Range("r1").Select
End Sub


Private Sub CommandButton5_Click()
Worksheets("Users Guide").Visible = True
Worksheets("Users Guide").Activate

End Sub

Private Sub Opt_Drained_Vol_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Drained Volume"
plotgraph (Worksheets("DMOutput").Range("H3:H103"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("H3:H103"))
End Sub


Private Sub Opt_Drought_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Drought (%) for Corn"
plotgraph (Worksheets("DMOutput").Range("Yield_drought"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("S3:S103"))
End Sub

Private Sub Opt_Dry_Days_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Dry Days"
plotgraph (Worksheets("DMOutput").Range("Dry_days"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("K3:K103"))
End Sub

Private Sub Opt_Evap_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Evapotranspiration"
plotgraph (Worksheets("DMOutput").Range("G3:G103"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("G3:G103"))
End Sub

Private Sub Opt_Excess_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Excess (%) for Corn"
plotgraph (Worksheets("DMOutput").Range("Yield_excess"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("R3:R103"))
End Sub

Private Sub Opt_Harvest_date_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Harvest Date"
plotgraph (Worksheets("DMOutput").Range("Harvest_date"))
End Sub

Private Sub Opt_Infiltration_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Infiltration"
plotgraph (Worksheets("DMOutput").Range("F3:F103"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("F3:F103"))
End Sub

Private Sub Opt_Overall_Yield_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Overall Yield (%) for Corn"
plotgraph (Worksheets("DMOutput").Range("Yield_Overall"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("V3:V103"))
End Sub

Private Sub Opt_Plant_Date_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Plant Date"
plotgraph (Worksheets("DMOutput").Range("Plant_date"))
End Sub

Private Sub Opt_Plant_Delay_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Plant Delay"
plotgraph (Worksheets("DMOutput").Range("Plant_delay"))
End Sub

Private Sub Opt_Rainfall_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Rainfall"
plotgraph (Worksheets("DMOutput").Range("Rainfall"))
End Sub

Private Sub Opt_Runoff_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Runoff"
plotgraph (Worksheets("DMOutput").Range("Runoff_in"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("I3:I103"))
End Sub

Private Sub Opt_Salinity_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Salinity (%)"
plotgraph (Worksheets("DMOutput").Range("Yield_Salinity"))
End Sub

Private Sub Opt_SDI_Excess_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "SDI Excess for Corn"
plotgraph (Worksheets("DMOutput").Range("SDI_excess"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("M3:M103"))
End Sub

Private Sub Opt_SEW_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Groundwater table 30cm above groundwater depth"
plotgraph (Worksheets("DMOutput").Range("SEW_30"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("L3:L103"))
End Sub

Private Sub Opt_Soy_Drought_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Drought (%) for Soybean"
plotgraph (Worksheets("DMOutput").Range("Soy_Drought"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("AC3:AC103"))
End Sub

Private Sub Opt_Soy_Excess_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Yield Excess (%) for Soybean"
plotgraph (Worksheets("DMOutput").Range("Soy_Excess"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("AB3:AB103"))
End Sub

Private Sub Opt_Soy_Overall_Yield_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Relative Overall Yield (%) for Soybean"
plotgraph (Worksheets("DMOutput").Range("Soy_Overall_Yield"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("AF3:AF103"))
End Sub

Private Sub Opt_Soy_SDI_excess_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "SDI Excess for Soybean"
plotgraph (Worksheets("DMOutput").Range("Soy_SDI_excess"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("W3:W103"))
End Sub

Private Sub Opt_Soy_Stress_Drought_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Stress Drought for Soybean"
plotgraph (Worksheets("DMOutput").Range("Soy_Stress_Drought"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("X3:X103"))
End Sub

Private Sub Opt_Stress_Drought_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Stress Drought for Corn"
plotgraph (Worksheets("DMOutput").Range("Stress_drought"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("N3:N103"))
End Sub

Private Sub Opt_Work_Days_Click()
Worksheets("Main").ChartObjects("Chart 11").Chart.ChartTitle.Text = "Work Days"
plotgraph (Worksheets("DMOutput").Range("Work_days"))
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then plotgraphDWM (Worksheets("DWMOutput").Range("J3:J103"))
End Sub



Private Sub OptionButton10_Click()

If OptionButton10.Value = True Then
  Sheets("Main").Range("Conductivity") = 0.0226
  Sheets("Main").Range("Porosity") = 0.53
  TextBox2.Text = OptionButton10.Caption
End If
  

End Sub

Private Sub OptionButton11_Click()

If OptionButton11.Value = True Then
  Sheets("Main").Range("Conductivity") = 0.0194
  Sheets("Main").Range("Porosity") = 0.47
  TextBox2.Text = OptionButton11.Caption
End If
  
End Sub

Private Sub OptionButton12_Click()

If OptionButton12.Value = True Then
  Sheets("Main").Range("Conductivity") = 0.0174
  Sheets("Main").Range("Porosity") = 0.47
  TextBox2.Text = OptionButton12.Caption
End If
  

End Sub

Private Sub OptionButton2_Click()
 Dim PltFile As String, NewPath As String
 MsgBox ("Select 'Run New Simulation', to run the model for simulated climate data (NASA NEX-GDDP-CMIP6: model historical run to " & NEX_HIST_END & ", then the scenario on the New Flow File sheet) for the period " & SIM_BASE_Y1 & "-" & SIM_BASE_Y2)
 
If OptionButton2.Value = True Then

  Worksheets("DMData").Range("FirstYear") = SIM_BASE_Y1
  Worksheets("DMdata").Range("LastYear") = SIM_BASE_Y2
     Worksheets("DMData").Range("TYear1") = SIM_BASE_Y1
     Worksheets("DMData").Range("TYear2") = SIM_BASE_Y2
     Worksheets("DMData").Range("RYear1") = SIM_BASE_Y1
     Worksheets("DMData").Range("RYear2") = SIM_BASE_Y2
     Worksheets("Main").Range("ClmCheck") = 0
     'Call Worksheets("New Flow File").DrainMod_Run
  
End If
End Sub
Private Sub OptionButton3_Click()
Dim PltFile As String, NewPath As String
MsgBox ("Select 'Run New Simulation', to run the model for simulated climate data (NASA NEX-GDDP-CMIP6, model and scenario on the New Flow File sheet) for the period 2040-2069")

If OptionButton3.Value = True Then
     
  Worksheets("DMData").Range("FirstYear") = 2040
  Worksheets("DMdata").Range("LastYear") = 2069
     
     Worksheets("DMData").Range("TYear1") = 2040
     Worksheets("DMData").Range("TYear2") = 2069
     Worksheets("DMData").Range("RYear1") = 2040
     Worksheets("DMData").Range("RYear2") = 2069

     Worksheets("Main").Range("ClmCheck") = 1
     'Call Worksheets("New Flow File").DrainMod_Run

End If
End Sub
Private Sub OptionButton1_Click()
Dim PltFile As String, NewPath As String
MsgBox ("Select 'Run New Simulation', to run the model for simulated climate data (NASA NEX-GDDP-CMIP6, model and scenario on the New Flow File sheet) for the period 2070-2099")

If OptionButton1.Value = True Then
     
  Worksheets("DMData").Range("FirstYear") = 2070
  Worksheets("DMdata").Range("LastYear") = 2099
     
     
     Worksheets("DMData").Range("TYear1") = 2070
     Worksheets("DMData").Range("TYear2") = 2099
     Worksheets("DMData").Range("RYear1") = 2070
     Worksheets("DMData").Range("RYear2") = 2099


     Worksheets("Main").Range("ClmCheck") = 2
     'Call Worksheets("New Flow File").DrainMod_Run

End If
End Sub

Private Sub OptionButton4_Click()
Dim PltFile As String, NewPath As String, y0 As Variant, y1 As Variant
y0 = Sheets("New Flow File").Range("ObsYear1").Value
y1 = Sheets("New Flow File").Range("ObsYear2").Value
If Not IsNumeric(y0) Or IsEmpty(y0) Then y0 = 1996
If Not IsNumeric(y1) Or IsEmpty(y1) Then y1 = 2025
MsgBox ("Select 'Run New Simulation', to run the model for observed climate data (Daymet) for the period " & y0 & "-" & y1)

 Worksheets("DMData").Range("FirstYear") = y0
 Worksheets("DMdata").Range("LastYear") = y1
     Worksheets("DMData").Range("TYear1") = y0
     Worksheets("DMData").Range("TYear2") = y1
     Worksheets("DMData").Range("RYear1") = y0
     Worksheets("DMData").Range("RYear2") = y1
     Worksheets("Main").Range("ClmCheck") = 3
     'Call Worksheets("New Flow File").DrainMod_Run
End Sub
