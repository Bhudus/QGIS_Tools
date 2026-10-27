"""Smoke test of the plugin wiring and algorithm with stubbed QGIS classes (no QGIS needed):
    python tests/test_plugin_stub.py
"""
import collections
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
PKG = os.path.basename(os.path.dirname(HERE))


def mod(n, **a):
    m = types.ModuleType(n)
    m.__dict__.update(a)
    sys.modules[n] = m
    return m


class Base(object):
    def __init__(self, *a, **k):
        self.a = a


class Alg(object):
    def addParameter(self, p):
        self.params = getattr(self, 'params', []) + [p]

    def addOutput(self, o):
        pass


class Prov(object):
    def addAlgorithm(self, a):
        self.algs = getattr(self, 'algs', []) + [a]


class Exc(Exception):
    pass


class Reg(object):
    def __init__(self):
        self.providers = []

    def addProvider(self, p):
        self.providers.append(p)
        p.loadAlgorithms()

    def removeProvider(self, p):
        self.providers.remove(p)


REG = Reg()
mod('qgis')
mod('qgis.PyQt')
mod('qgis.PyQt.QtCore', QCoreApplication=type('Q', (), {'translate': staticmethod(lambda c, s: s)}))
mod('qgis.PyQt.QtGui', QIcon=lambda p: None)
mod('qgis.core', Qgis=type('Qgis', (), {}),
    QgsApplication=type('App', (), {'processingRegistry': staticmethod(lambda: REG)}),
    QgsFeatureRequest=type('R', (), {'NoGeometry': 1, 'setFlags': lambda s, f: s,
                                     'setSubsetOfAttributes': lambda s, a: s}),
    QgsFeatureSink=type('S', (), {'FastInsert': 1}),
    QgsProcessing=type('P', (), {'TypeVectorPoint': 0}), QgsProcessingAlgorithm=Alg,
    QgsProcessingProvider=Prov, QgsProcessingException=Exc, QgsProcessingOutputNumber=Base,
    QgsProcessingParameterEnum=Base, QgsProcessingParameterFeatureSink=Base,
    QgsProcessingParameterFeatureSource=Base,
    QgsProcessingParameterNumber=type('N', (Base,), {'Integer': 0, 'Double': 1}))

pkg = __import__(PKG)
plugin = pkg.classFactory(object())
plugin.initGui()
assert len(REG.providers) == 1
prov = REG.providers[0]
assert prov.id() == 'randompointselection' and len(prov.algs) == 1
alg = prov.algs[0].createInstance()
alg.initAlgorithm()
print('provider:', prov.name(), '| algorithm:', alg.displayName(), '| group:', alg.group(),
      '| params:', [p.a[0] for p in alg.params])


class Feat(object):
    def __init__(self, i):
        self.i = i

    def id(self):
        return self.i


class Src(object):
    def __init__(self, n):
        self.n = n

    def getFeatures(self, req=None):
        return iter([Feat(i) for i in range(self.n)])

    def fields(self):
        return 'F'

    def wkbType(self):
        return 1

    def sourceCrs(self):
        return 'C'


class Sink(object):
    def __init__(self):
        self.ids = []

    def addFeature(self, f, fl):
        self.ids.append(f.id())


class FB(object):
    def __init__(self):
        self.m = []

    def pushInfo(self, m):
        self.m.append(m)

    def pushWarning(self, m):
        self.m.append('WARN ' + m)

    def setProgress(self, p):
        pass

    def isCanceled(self):
        return False


def run(n, method, number=0, pct=0.0, seed=None, rest=False):
    a = prov.algs[0].createInstance()
    sinks = {}

    def sink(p, k, c, *args):
        sinks[k] = Sink()
        return sinks[k], k
    a.parameterAsSource = lambda p, k, c: p[k]
    a.parameterAsEnum = lambda p, k, c: p[k]
    a.parameterAsInt = lambda p, k, c: p[k]
    a.parameterAsDouble = lambda p, k, c: p[k]
    a.parameterAsSink = sink
    a.invalidSourceError = a.invalidSinkError = lambda *x: 'bad'
    params = {'INPUT': Src(n), 'METHOD': method, 'NUMBER': number, 'PERCENTAGE': pct,
              'SEED': seed, 'OUTPUT': 'OUTPUT'}
    if rest:
        params['OUTPUT_UNSELECTED'] = 'OUTPUT_UNSELECTED'
    fb = FB()
    res = a.processAlgorithm(params, None, fb)
    return res, sinks, fb


res, s, fb = run(1000, 0, number=50, seed=1)
sel = s['OUTPUT'].ids
assert len(sel) == len(set(sel)) == 50 == res['SELECTED_COUNT']
assert run(1000, 0, number=50, seed=1)[1]['OUTPUT'].ids == sel
assert run(1000, 0, number=50, seed=2)[1]['OUTPUT'].ids != sel
res, s, fb = run(1000, 1, pct=12.5, seed=3, rest=True)
a_, b_ = set(s['OUTPUT'].ids), set(s['OUTPUT_UNSELECTED'].ids)
assert len(a_) == 125 and not (a_ & b_) and (a_ | b_) == set(range(1000))
assert len(run(5, 0, number=20)[1]['OUTPUT'].ids) == 5
assert len(run(20, 1, pct=1.0)[1]['OUTPUT'].ids) == 1
assert len(run(20, 1, pct=0.0)[1]['OUTPUT'].ids) == 0
assert len(run(20, 1, pct=100.0)[1]['OUTPUT'].ids) == 20
try:
    run(0, 0, number=5)
    raise SystemExit('empty layer should fail')
except Exc:
    pass
c = collections.Counter()
for t in range(2000):
    c.update(run(100, 0, number=10, seed=t)[1]['OUTPUT'].ids)
print('uniformity (expected 200): min %d max %d, hit %d/100' % (min(c.values()), max(c.values()), len(c)))
plugin.unload()
assert not REG.providers
print('OK')
