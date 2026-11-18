import sys, time, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hydro as h
n=200
y,x=np.mgrid[0:n,0:n].astype(float)
dem = 0.05*abs(x-100) + 0.02*(n-y)          # V-valley along x=100 draining south
dem[60:70,30:40] -= 3                          # a pit
dem[120:140, 140:160] = dem[120:140,140:160].min()  # a flat
valid = np.ones_like(dem,bool); valid[:, :5]=False   # nodata strip
t=time.time(); zf=h.fill_depressions(dem,valid); print('fill',time.time()-t)
rcv=h.flow_directions(zf,valid,10.0)
acc=h.flow_accumulation(zf,valid,rcv)
stream = acc>2000
print('stream cols', np.unique(np.nonzero(stream)[1])[:10])
field=np.zeros_like(valid); field[50:80,20:50]=True  # covers the pit
outs=h.field_outlets(field,stream,zf,rcv)
print('outlets',outs[:3], 'sum',sum(a for _,a in outs), field.sum())
cells,why=h.trace_path(outs[0][0],rcv,stream)
print(why,len(cells),divmod(cells[0],n),divmod(cells[-1],n))
# no cycles / strictly decreasing
zz=zf.ravel()[cells]; assert np.all(np.diff(zz)<0)
# every valid interior cell has receiver
b=h.boundary_cells(valid)
assert np.all(rcv[(valid&~b).ravel()]>=0)
# field crossing stream
field2=np.zeros_like(valid); field2[150:170,90:110]=True
o2=h.field_outlets(field2,stream,zf,rcv); print('o2',o2[:3])
t=time.time(); big=np.random.rand(1000,1000); v=np.ones_like(big,bool)
zf=h.fill_depressions(big,v); r=h.flow_directions(zf,v); a=h.flow_accumulation(zf,v,r); print('1M cells',time.time()-t, a.max())
