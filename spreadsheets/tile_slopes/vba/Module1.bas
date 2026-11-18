Attribute VB_Name = "Module1"
Function Elevation(x As Single, Table As Range, _
   YCol As Integer)
    
    'Returns the interpolated number from the first column of
   'Table and the YCol column of table.
   'x - the number between two numbers in the first column of Table
   'Table - The range of values with at least YCol columns
   'YCol - the column of Table used for the Y-side of the interpolation
   
    
   Dim TableRow As Long, Temp As Variant
   Dim x0 As Double, x1 As Double, y0 As Double, y1 As Double
    
    
    'TableRow - the row of Table that is less than or equal to x
   'x0, x1, y0, y1 - the coordinates to interpolate
   
   
    'skip the error when match returns N/A
   On Error Resume Next
    
    'Find the row in Table that's less than or equal to x
   Temp = Application.WorksheetFunction.Match(x, Table.Resize(, 1), 1)
    
    'If Match returns an error, return the error
   If IsError(Temp) Then
        FlowParameters = CVErr(Temp)
    Else

       TableRow = Temp
        
        'The first coordinates are the from the row just less than x
       x0 = Table(TableRow, 1)
        y0 = Table(TableRow, YCol)
        
        'If x is the same as the row just less than (or equal to) x,
       'return the value from YCol
       If x = x0 Then
            Elevation = y0
        Else
            'Get the second coordinates from the next row
           x1 = Table(TableRow + 1, 1)
            y1 = Table(TableRow + 1, YCol)
            'Do the math
           Elevation = (x - x0) / (x1 - x0) * (y1 - y0) + y0
        End If
    End If
End Function

Function TileDepth(L1, L2, Upper, Lower, minSlope, maxSlope, AbsU, AbsL, Surface, check1, overide, cstart, cend, D0)

 

If L2 = L1 Then
  TD = D0
 GoTo 100
End If

MD = (Upper + Lower) / 2


asl = (MD - D0) / (L2 - L1)


 
           If check1 = 1 And L2 > cstart And L2 < cend Then
             TD = D0 + (L2 - L1) * overide / 100
             GoTo 100
          End If
 
 Select Case asl
    Case Is < minSlope
        TD = D0 + (L2 - L1) * minSlope
        If TD < Surface - AbsL Then TD = Surface - AbsL
        
    Case Is > maxSlope
        TD = D0 + (L2 - L1) * maxSlope
        If TD > Surface - AbsU Then TD = Surface - AbsU
        
    Case Else
         TD = MD
        
      
 End Select
 

 
100 TileDepth = TD

End Function
