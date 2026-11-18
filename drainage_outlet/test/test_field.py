import sys, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')); import hydro as h
n=120; y,x=np.mgrid[0:n,0:n].astype(float)
dem = 0.05*np.abs(x-90) + 0.03*(n-y)        # valley at col 90 draining south
valid=np.ones((n,n),bool)
zf=h.fill_depressions(dem,valid); rcv=h.flow_directions(zf,valid,10)
stream=np.zeros_like(valid); stream[:,89:92]=True      # buffered stream, 3 cells wide
# field touching the stream buffer on its east edge
field=np.zeros_like(valid); field[20:40,60:91]=True
old=h.field_outlets(field,stream,zf,rcv); new=h.field_final_outlets(field,stream,rcv)
print('old primary',divmod(old[0][0],n),'new primary',divmod(new[0][0],n), new[0][1], field.sum())
o=new[0][0]; cells,why=h.trace_path(o,rcv,stream&~field)
print('path',why,[divmod(c,n) for c in cells][:4])
assert not field.ravel()[cells[1]]   # leaves the field right after the outlet
# concave U-field: flow exits the west arm and re-enters the east arm
dem2 = 0.05*(n-x)+0.001*y; zf2=h.fill_depressions(dem2,valid); r2=h.flow_directions(zf2,valid,10)
U=np.zeros_like(valid); U[30:60,20:30]=True; U[30:60,50:60]=True; U[55:60,20:60]=True
s2=np.zeros_like(valid); s2[:,115:]=True
new2=h.field_final_outlets(U,s2,r2)
print('U outlets',[(divmod(i,n),k) for i,k in new2[:3]], 'sum',sum(k for _,k in new2),U.sum())
for o,_ in new2:
    cells,_=h.trace_path(o,r2,s2&~U); assert not U.ravel()[cells[1:]].any()
print('OK')
