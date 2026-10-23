"""Scalar pose/URDF-data arithmetic only; no configuration-dependent FK or SDK."""
import math
import xml.etree.ElementTree as ET


def unit(q):
    length=math.hypot(*q)
    assert math.isfinite(length) and length>0
    return [value/length for value in q]


def product(a,b):
    x,y,z,w=a; X,Y,Z,W=b
    return [w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z]


def rotation(q):
    x,y,z,w=unit(q)
    return [[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
            [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
            [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]


def rotate(q,v):
    return [sum(x*y for x,y in zip(row,v,strict=True)) for row in rotation(q)]


def cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


def axis_line_error(actual_point,actual_direction,expected_point,expected_direction):
    """Static oriented lines, already expressed in the same registered parent frame."""
    actual=unit(actual_direction);expected=unit(expected_direction)
    delta=[a-b for a,b in zip(actual_point,expected_point,strict=True)]
    actual_moment=cross(actual_point,actual);expected_moment=cross(expected_point,expected)
    expected_distance=math.hypot(*cross(delta,expected))
    actual_distance=math.hypot(*cross(delta,actual))
    return {'axisDifference':math.hypot(*(a-b for a,b in zip(actual,expected,strict=True))),
            'axisLinePerpM':max(expected_distance,actual_distance),
            'anchorToExpectedLineM':expected_distance,'anchorToActualLineM':actual_distance,
            'actualAxisMoment':actual_moment,'expectedAxisMoment':expected_moment,
            'momentDifferenceM':math.hypot(*(a-b for a,b in zip(actual_moment,expected_moment,strict=True)))}


def compose(a,b):
    offset=rotate(a[3:],b[:3])
    return [a[i]+offset[i] for i in range(3)]+unit(product(unit(a[3:]),unit(b[3:])))


def inverse(a):
    q=unit(a[3:]); q=[-q[0],-q[1],-q[2],q[3]]
    return rotate(q,[-value for value in a[:3]])+q


def pose_matrix(pose):
    return [row+[pose[i]] for i,row in enumerate(rotation(pose[3:]))]+[[0.,0.,0.,1.]]


def matrix_error(a,b):
    distance=math.hypot(*(a[i][3]-b[i][3] for i in range(3)))
    frobenius=math.sqrt(sum((a[i][j]-b[i][j])**2 for i in range(3) for j in range(3)))
    return {'positionM':distance,'angleRad':2*math.asin(min(1.,frobenius/(2*math.sqrt(2))))}


def rebase_prediction(matrix,declared_base,observed_base):
    # The pinned declared base has identity rotation. Do not fit it from TCP samples.
    assert all(declared_base[i][j]==(1. if i==j else 0.) for i in range(3) for j in range(3))
    r=rotation(observed_base[3:]); local=[matrix[i][3]-declared_base[i][3] for i in range(3)]
    result=[[sum(r[i][k]*matrix[k][j] for k in range(3)) for j in range(3)]
            +[observed_base[i]+sum(r[i][k]*local[k] for k in range(3))] for i in range(3)]
    return result+[[0.,0.,0.,1.]]


def urdf_chain(path):
    by_child={j.find('child').get('link'):j for j in ET.parse(path).getroot().findall('joint')}
    chain=[]; link='fr3_hand_tcp'
    while link in by_child:
        joint=by_child[link]; origin=joint.find('origin'); axis=joint.find('axis')
        xyz=[float(v) for v in origin.get('xyz','0 0 0').split()]
        rpy=[float(v) for v in origin.get('rpy','0 0 0').split()]
        roll,pitch,yaw=rpy
        qx=[math.sin(roll/2),0.,0.,math.cos(roll/2)]
        qy=[0.,math.sin(pitch/2),0.,math.cos(pitch/2)]
        qz=[0.,0.,math.sin(yaw/2),math.cos(yaw/2)]
        chain.insert(0,{'name':joint.get('name'),'type':joint.get('type'),
            'parent':joint.find('parent').get('link'),'child':joint.find('child').get('link'),
            'xyz':xyz,'rpy':rpy,'originPose':xyz+unit(product(product(qz,qy),qx)),
            'axis':None if axis is None else [float(v) for v in axis.get('xyz').split()]})
        link=joint.find('parent').get('link')
    assert len(chain)==11 and link=='base'
    return chain
