Attribute VB_Name = "formSpacing"
Attribute VB_Base = "0{969862D6-1A1C-46EA-BDC0-DB1E0A4EEABE}{8CD5B040-D299-4570-BC6C-562DFCB95DCB}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = False
Option Explicit
Dim DDepth As Single, IDepth As Single, MDepth As Single, K As Single, DC As Single
Dim m As Single, d As Single, S As Single, diff As Single, de As Single, Sp As Single
Dim t As Single, TDepth As Single, TIDepth As Single, TIMDepth As Single, re As Single
Dim TFMDepth As Single, f As Single, m0 As Single, ge As Single, pi As Single
Dim y0 As Single

Private Sub cmdCalculate_Click()
    DDepth = Val(txtDrainDepth.Text)
        txtSdd.Text = Str(DDepth): txtKb.Text = Str(DDepth)
    IDepth = Val(txtImpDepth.Text)
    MDepth = Val(txtMidDepth.Text)
    K = Val(txtHydCon.Text)
         txtSK.Text = Str(K): txtKK.Text = Str(K)
    m = DDepth - MDepth
    txtSM.Text = Str(m): txtKt.Text = Str(MDepth)
    d = IDepth - DDepth
    txtSd.Text = Str(d): txtKd.Text = Str(d)
    pi = 4 * Atn(1)
    S = 100: Sp = 100
    diff = 20
    

     
    re = EffectiveRadius

If optSSpacing.Value = True Then
'Calculating Required Spacing
    DC = Val(txtDCoeff.Text) / 12 'converting in/day to ft/day
    
    If optHoog Then
    
      While diff > 1
        de = effdpt2(d, S, re)
        Sp = 4 * K * m * (m + 2 * de) / DC
        Sp = Sqr(Sp)
        diff = Abs(Sp - S)
        S = Sp
      Wend
    End If
    
    If optKirk Then
      MDepth = MDepth / 12
      While diff > 0.1
        ge = KirkamG2(d, IDepth, re, S)
        Sp = 4 * pi * K * (MDepth + DDepth - re) / DC / ge
        'Sp = Sqr(Sp) :
        'If Sp > 200 Then Sp = 200
        diff = Abs(Sp - S)
        S = Sp
      Wend
    End If
       S = Int(10 * S + 0.5) / 10

    txtdeS.Text = Str(de)
        txtSde.Text = Str(de)
        txtSS.Text = Str(S): txtKS.Text = Str(S)
    txtSpacing.BackColor = &H80000005
    txtSpacing.Enabled = True
    txtSpacing.Text = Str(S)
    
    
ElseIf optDC.Value = True Then
'Calculating Drainage Coefficient
    S = Val(txtSpacing.Text)
    
    If optHoog Then
         de = effdpt2(d, S, re)
        DC = 4 * K * m * (m + 2 * de) / S ^ 2
    End If
   
   If optKirk Then
        MDepth = MDepth / 12
        ge = KirkamG2(d, IDepth, re, S)
        DC = 4 * pi * K * (MDepth + DDepth - re) / S / ge
   End If
   
    DC = DC * 12 'converting ft/day to in/day
    txtdeS.Text = Str(de)
    txtDCoeff.BackColor = &H80000005
    txtDCoeff.Enabled = True
    txtDCoeff.Text = Format(DC, "##0.0##")
        txtSS.Text = Str(S): txtKS.Text = Str(S)
End If

End Sub

Private Sub cmdHydCon_Click()
frmHydCon.Visible = True
End Sub

Private Sub cmdHydConn_Click()
frmHydCon.Visible = True
End Sub

Private Sub cmdSbCalculate_Click()
    DDepth = Val(txtSDrainDepth.Text)
        txtSbdd.Text = Str(DDepth): txtKb.Text = Str(DDepth)
    IDepth = Val(txtSImpDepth.Text)
    MDepth = Val(txtMidDepth.Text)
    K = Val(txtSHydCon.Text)
         txtSbK.Text = Str(K): txtKK.Text = Str(K)
    If Val(txtICoeff.Text) <= 0 Then
       MsgBox ("Irrigation coefficient must be greater than zero (in/day)")
       Exit Sub
    End If
    If IDepth <= DDepth Then
       MsgBox ("Depth to impermeable layer must be greater than drain depth")
       Exit Sub
    End If
    y0 = Val(txtOverDrain.Text): txtSbY0.Text = Str(y0)
    m = Val(txtMidDrain.Text) - y0: txtSbM.Text = txtMidDrain.Text
    If m >= 0 Then
       MsgBox ("Mid-drain height should be less than over-drain height")
       Exit Sub
    End If
       
    txtSM.Text = Str(m): txtKt.Text = Str(MDepth)
    d = IDepth - DDepth
    txtSbd.Text = Str(d): txtKd.Text = Str(d)
    pi = 4 * Atn(1)
    S = 100: Sp = 100
    diff = 20
    

     
    re = EffectiveRadius


'Calculating Required Spacing
    DC = -Val(txtICoeff.Text) / 12 'converting in/day to ft/day
    
        
      While diff > 0.5
        de = effdpt2(d, S, re)
        Sp = 4 * K * m * (m * (y0 + de) / (y0 + d) + 2 * (y0 + de)) / DC
        Sp = Sqr(Sp)
        diff = Abs(Sp - S)
        S = Sp
      Wend
    
       S = Int(10 * S + 0.5) / 10

    txtdeS.Text = Str(de)
        txtSbde.Text = Str(de)
        txtSbS.Text = Str(S): txtKS.Text = Str(S)
    txtSbSpacing.BackColor = &H80000005
    txtSbSpacing.Enabled = True
    txtSbSpacing.Text = Str(S)
    

End Sub

Private Sub cmdShow_Click()
Dim ipage As Integer
  cmdShow.Caption = "SHOW DIAGRAM"

With PictureForm

    .Width = 500
    .Height = 260
    .picSteady.Visible = False
    .picTransient.Visible = False
    .picSubIrrigate.Visible = False
    .picSpacing.Visible = False
    
    ipage = tabSpacing.Value
    Select Case ipage
        Case 0
            .picSteady.Visible = True
        Case 1
            .picTransient.Visible = True
        Case 2
            .picSubIrrigate.Visible = True
        Case Else
            .picSteady.Visible = True
   End Select
    .Show
End With

End Sub

Private Sub cndHydCon_Click()
frmHydCon.Visible = True
End Sub

Private Sub Command1_Click()
txtdeT.Visible = False
txtdeS.Visible = False
End Sub

Private Sub Command2_Click()
txtdeT.Visible = True
txtdeS.Visible = True
End Sub



Private Sub FormSpacing_Load()
Me.Width = 313
Me.Height = 431
cmdShow.Caption = "Show Diagram"
txtImpDepth.Text = Worksheets("Spacing").Range("SSDepth")
txtTImpDepth.Text = Worksheets("Spacing").Range("SSDepth")
txtSImpDepth.Text = Worksheets("Spacing").Range("SSDepth")

txtTDrainPor.Text = Worksheets("Spacing").Range("DPorosity")

txtHydCon.Text = Worksheets("Spacing").Range("Conductivity")
txtTHydCon.Text = Worksheets("Spacing").Range("Conductivity")
txtTydCon.Text = Worksheets("Spacing").Range("Conductivity")


tabSpacing.Value = 1
  frapicSteady.Visible = False
  frapicKirkham.Visible = False
  frapicTransient.Visible = True
  frapicSubirrigate.Visible = False
End Sub



Private Sub Frame2_Click()

End Sub

Private Sub optHoog_Click()
If optHoog Then lblMidplane.Caption = "Depth to mid-drain  water table (ft)"
  If frapicKirkham.Visible = True Then
        frapicSteady.Visible = True
       frapicKirkham.Visible = False
  End If
End Sub

Private Sub optKirk_Click()
If optKirk Then lblMidplane.Caption = "Ponded depth above ground surface (in)"
  If frapicSteady.Visible = True Then
       frapicSteady.Visible = False
       frapicKirkham.Visible = True
  End If

End Sub

Private Sub optSSpacing_Click()
    txtSpacing.BackColor = &H8000000F
    txtSpacing.Enabled = False
    txtDCoeff.BackColor = &H80000005
    txtDCoeff.Enabled = True
End Sub

Private Sub optDC_Click()
    txtSpacing.BackColor = &H80000005
    txtSpacing.Enabled = True
    txtDCoeff.BackColor = &H8000000F
    txtDCoeff.Enabled = False
End Sub

Function effdpt2(d As Single, L As Single, r0 As Single)

Dim pi As Single, X As Single, fx As Single, dfx As Single
Dim n As Integer

pi = 4 * Atn(1)
X = 2 * pi * d / L
Select Case X
   Case Is < 0.000001
      de = d
   Case 0.000001 To 0.5
      fx = pi * pi / 4 / X + Log(X / 2 / pi)
      de = pi * L / 8 / (Log(L / pi / r0) + fx)
   Case Is > 0.5
      fx = 0: dfx = 1: n = 1
      While dfx > 0.00001
        dfx = 4 * Exp(-2 * n * X) / (n * (1 - Exp(-2 * n * X)))  'p213 eq(11): F(x) = 4*Sum[(1/n)*e^(-2nx)/(1-e^(-2nx))], n = 1,3,5...
        fx = fx + dfx
        n = n + 2
      Wend
      de = pi * L / 8 / (Log(L / pi / r0) + fx)
End Select

effdpt2 = de
End Function

Private Sub cmdQuit_Click()
    End
End Sub

Private Sub cmdTCal_Click()
    
    TDepth = Val(txtTDrainDepth.Text)
    txtTdd.Text = Str(TDepth)
    TIDepth = Val(txtTImpDepth.Text)
    TIMDepth = Val(txtTIMidDepth.Text)
    TFMDepth = Val(txtTFMidDepth.Text)
    K = Val(txtTHydCon.Text)
    txtTk.Text = Str(K)
    f = Val(txtTDrainPor.Text)
    txtTf.Text = Str(f)
    m0 = TDepth - TIMDepth
        txtTm0.Text = Str(m0)
    m = TDepth - TFMDepth
        txtTm1.Text = Str(m)
    d = TIDepth - TDepth
        txtTd.Text = Str(d)
        re = EffectiveRadius
    If optSpacing.Value = True Then
        calSpacing                 'Calculating Drain Space
    ElseIf optTime.Value = True Then
        calTime                    'Calculating Time
    End If
    
End Sub
Sub calSpacing()
    
    t = Val(txtTime.Text)
    S = 100: Sp = 100
    diff = 20

'Elliptic
    If optElliptic.Value = True Then
    While diff > 1
        de = effdpt2(d, S, re)

        Sp = 10 * K * de * t / f * 1 / (Log(m0 * (2 * de + m) / (m * (2 * de + m0))))
        Sp = Sqr(Sp)
        diff = Abs(Sp - S)
        S = Sp
    Wend
    End If
'Parabolic
    If optParabolic.Value = True Then
    While diff > 1
        de = effdpt2(d, S, re)

        Sp = 10 * K * de * t / f * 1 / (Log(1.16 * m0 / m))
        Sp = Sqr(Sp)
        diff = Abs(Sp - S)
        S = Sp
    Wend
    End If
    txtdeT.Text = Str(de)
    txtTde.Text = Str(de)
    txtTSpacing.BackColor = &H80000005
    txtTSpacing.Enabled = True
    txtTSpacing.Text = Str(S)
    txtTs.Text = Str(S)

End Sub

Sub calTime()

    S = Val(txtTSpacing)
    de = effdpt2(d, S, re)
    
'Elliptic
    If optElliptic.Value = True Then
        t = f * S ^ 2 / (10 * K * de) * (Log(m0 * (2 * de + m) / (m * (2 * de + m0))))
    End If
'Parabolic

    If optParabolic.Value = True Then
        t = f * S ^ 2 / (10 * K * de) * (Log(1.16 * m0 / m))
    End If
    txtdeT.Text = Str(de)
    txtTime.BackColor = &H80000005
    txtTime.Enabled = True
    txtTime.Text = Format(t, "##0.0##")
    
End Sub
Private Sub cmdTReset_Click()

  Me.Width = 313
  cmdShow.Caption = "Show Diagram"
ComboSize.Text = "Select Drain Diameter"
  optHoog = True
  lblMidplane.Caption = "Depth to mid-drain  water table (ft)"
    
If tabSpacing.Value = 1 Then
    optSpacing.Value = True
    optTime.Value = False
    
    txtTime.Text = "1"
    txtTDrainDepth.Text = "4"
    txtTImpDepth.Text = "6"
    txtTIMidDepth.Text = "0"
    txtTFMidDepth.Text = "1"
    txtTHydCon.Text = "3"
    txtTDrainPor.Text = "0.03"
    
    txtTime.BackColor = &H80000005
    txtTime.Enabled = True

    txtTSpacing.BackColor = &H8000000F
    txtTSpacing.Enabled = False
    txtTSpacing.Text = "80"
Else
    optSSpacing.Value = True
    optDC.Value = False
    
    txtDrainDepth.Text = "4"
    txtImpDepth.Text = "6"
    txtMidDepth.Text = "0"
    txtHydCon.Text = "3"
    txtDCoeff.Text = "0.375"
    
    txtDCoeff.BackColor = &H80000005
    txtDCoeff.Enabled = True
    
    txtSpacing.BackColor = &H8000000F
    txtSpacing.Enabled = False
    txtSpacing.Text = "80"
End If

End Sub
Private Sub optSpacing_Click()
    txtTSpacing.BackColor = &H8000000F
    txtTSpacing.Enabled = False
    txtTime.BackColor = &H80000005
    txtTime.Enabled = True
End Sub

Private Sub optTime_Click()
    txtTSpacing.BackColor = &H80000005
    txtTSpacing.Enabled = True
    txtTime.BackColor = &H8000000F
    txtTime.Enabled = False
End Sub

Public Function CosH(X As Double) As Double
If X > 200 Then X = 200
CosH = (Exp(X) + Exp(-X)) / 2
End Function
Public Function SinH(X As Double) As Double
If X > 200 Then X = 200
SinH = (Exp(X) - Exp(-X)) / 2
End Function



Public Function KirkamG(d As Single, h As Single, r As Single, L As Single) As Double
Dim pi As Single, g1 As Double, g2 As Double
Dim g2n1 As Double, g2n2 As Double, g2d1 As Double, g2d2 As Double

pi = 4 * Atn(1)

g1 = 2 * Log(Tan(pi * (2 * d - r) / 4 / h) / Tan(pi * r / 4 / h))
g2 = 0
For m = 1 To 20
  g2n1 = CosH(pi * m * L / 2 / h) + Cos(pi * r / 2 / h)
  g2d1 = CosH(pi * m * L / 2 / h) - Cos(pi * r / 2 / h)
  g2n2 = CosH(pi * m * L / 2 / h) - Cos(pi * (2 * d - r) / 2 / h)
  g2d2 = CosH(pi * m * L / 2 / h) + Cos(pi * (2 * d - r) / 2 / h)
  
  g2 = g2 + Log(g2n1 / g2d1 * g2n2 / g2d2)
 Next m
 KirkamG = g1 + 2 * g2
End Function

Public Function KirkamG2(d As Single, h As Single, r As Single, L As Single) As Double
Dim pi As Single, g1 As Double, g2 As Double
Dim g2n1 As Double, g2n2 As Double, g2d1 As Double, g2d2 As Double

pi = 4 * Atn(1)

g1 = 2 * Log(SinH(pi * (2 * d - r) / L) / SinH(pi * r / L))
g2 = 0
For m = 1 To 20
  g2n1 = (SinH(2 * pi * m * h / L)) ^ 2 - (SinH(pi * r / L)) ^ 2
  g2d1 = (SinH(2 * pi * m * h / L)) ^ 2 - (SinH(pi * (2 * d - r) / L)) ^ 2

  
  g2 = g2 * 1 ^ m * Log(g2n1 / g2d1)
 Next m
 KirkamG2 = g1 - 2 * g2
End Function

Function EffectiveRadius()
' Drain diameter fixed at 4 inches (most laterals are 4-in corrugated tubing).
' Effective radius 0.017 ft per NRCS NEH 624 Ch. 10, Table 10-1a (Skaggs, 1980).
    EffectiveRadius = 0.017
    txtS2r.Text = Str(4): txtSb2r.Text = Str(4): txtK2r.Text = Str(4): txtT2r.Text = Str(4)
End Function

Private Sub tabSpacing2_click()
If tabSpacing.Value = 0 Then

  If optHoog Then
       frapicSteady.Visible = True
       frapicKirkham.Visible = False
  Else
       frapicSteady.Visible = False
       frapicKirkham.Visible = True
  End If
    frapicSubirrigate.Visible = False
    frapicTransient.Visible = False
End If
If tabSpacing.Value = 1 Then
  frapicSteady.Visible = False
  frapicKirkham.Visible = False
  frapicTransient.Visible = True
  frapicSubirrigate.Visible = False
  
End If

If tabSpacing.Value = 2 Then
  frapicSteady.Visible = False
  frapicKirkham.Visible = False
  frapicSubirrigate.Visible = True
  frapicTransient.Visible = False
  
End If
End Sub




Private Sub tab_Spacing_click(PreviousTab As Integer)
If tabSpacing.Value = 0 Then

  If optHoog Then
       frapicSteady.Visible = True
       frapicKirkham.Visible = False
  Else
       frapicSteady.Visible = False
       frapicKirkham.Visible = True
  End If
  
  frapicSubirrigate.Visible = False
  frapicTransient.Visible = False
End If
If tabSpacing.Value = 1 Then
  frapicSteady.Visible = False
  frapicKirkham.Visible = False
  frapicTransient.Visible = True
  frapicSubirrigate.Visible = False
End If

If tabSpacing.Value = 2 Then
  frapicSteady.Visible = False
  frapicKirkham.Visible = False
  frapicTransient.Visible = False
  frapicSubirrigate.Visible = True
End If

End Sub




