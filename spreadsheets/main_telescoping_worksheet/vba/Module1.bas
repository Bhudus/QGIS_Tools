Attribute VB_Name = "Module1"
Function Flowrate(DCoeff, Length, Spacing, UpArea, Apsize, Apslope, Aptype, Inflow, InDiameter, InHead) As Single

'Lateral flow
  LateralFlow = Length * Spacing * DCoeff / 12 / 24 / 60 / 60

'Upstream Flow
  UpstreamFlow = UpArea * DCoeff / 12 * 43560 / 24 / 60 / 60
  
'Attached Pipe Flow
  nn = Roughness(Aptype, Apsize)
  ApFlow = 1.49 / nn * 3.142 / 10.13 * (Apsize / 12) ^ (8 / 3) * (Apslope / 100) ^ 0.5
  
'Direct inflow
  Directflow = Inflow
  
'Inlet Flow
  InletFlow = 12 * InDiameter * InDiameter * Sqr(InHead) / 448.8325660485
  
Flowrate = LateralFlow + UpstreamFlow + Directflow + ApFlow + InletFlow
End Function


Function NominalPipeSize(ActualPipesize)
aps = ActualPipesize
If aps <= 3 Then ps = 3
If aps > 3 And aps <= 4 Then ps = 4
If aps > 4 And aps <= 5 Then ps = 5
If aps > 5 And aps <= 6 Then ps = 6
If aps > 6 And aps <= 8 Then ps = 8
If aps > 8 And aps <= 10 Then ps = 10
If aps > 10 And aps <= 12 Then ps = 12
If aps > 12 And aps <= 15 Then ps = 15
If aps > 15 And aps <= 18 Then ps = 18
If aps > 18 And aps <= 21 Then ps = 21
If aps > 21 And aps <= 24 Then ps = 24
If aps > 24 And aps <= 30 Then ps = 30

NominalPipeSize = ps
End Function

Function Pipesize(Flow, Slope, PType)
Slp = Slope / 100
Select Case PType
   Case 2, 3, 4
     nn = Roughness(PType, 1)
     psize = (Flow * nn * 4 ^ (5 / 3) / (1.49 * 3.142 * Slp ^ 0.5)) ^ (3 / 8)
     psize = psize * 12
Case Else
   'single wall pipe
    k = 2
100  k = k + 4
     nn = Roughness(PType, k)
     psize = (Flow * nn * 4 ^ (5 / 3) / (1.49 * 3.142 * Slp ^ 0.5)) ^ (3 / 8)
     psize = psize * 12
          d = psize / 12
         nn = Roughness(1, d)
     flow1 = 1.49 * 3.142 * Slp ^ 0.5 / (nn * 4 ^ (5 / 3)) * d ^ (8 / 3)
         
     If Abs(Flow - flow1) > 0.001 Then GoTo 100

     
End Select
Pipesize = psize
End Function


Function Roughness(PipeType, Pipesize) As Single
Select Case PipeType
  Case 1
    If Pipesize <= 8 Then Roughness = 0.015
    If Pipesize > 8 And Pipesize <= 12 Then Roughness = 0.017
    If Pipesize > 12 Then Roughness = 0.02
  Case 4, 3
    Roughness = 0.013
  Case 2
    Roughness = 0.012   ' dual wall

 Case Else
    If Pipesize <= 8 Then Roughness = 0.015
    If Pipesize > 8 And Pipesize <= 12 Then Roughness = 0.017
    If Pipesize > 12 Then Roughness = 0.02
End Select
End Function
