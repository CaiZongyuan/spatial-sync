"""PLANNED pure-data checks. No adapter, SDK, process, configuration q or chain FK."""
import hashlib
import contextlib
import io
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from pose_data import axis_line_error,compose,inverse,matrix_error,pose_matrix
from terminal_data import NonFiniteObservation,publish_terminal,record_numeric_vector,record_observation


def run():
    forbidden=lambda:[name for name in sys.modules if name.split('.')[0] in {'newton','warp','numpy','scipy'}
                       or name.startswith('tools.teach')]
    assert not forbidden()
    identity=[0.,0.,0.,0.,0.,0.,1.]
    perpendicular=[.02,0.,0.,0.,0.,0.,1.]
    parallel=[0.,0.,.03,0.,0.,0.,1.]
    # Fixed registration factors only. Do not evaluate Rot(q) or any kinematic chain.
    rest=[compose(pose,inverse(pose)) for pose in (identity,perpendicular,parallel)]
    lines=[axis_line_error(pose[:3],[0,0,1],identity[:3],[0,0,1])
           for pose in (identity,perpendicular,parallel)]
    assert all(matrix_error(pose_matrix(value),pose_matrix(identity))=={'positionM':0.,'angleRad':0.}
               for value in rest)
    assert all(value['axisDifference']==0 for value in lines)
    assert lines[1]['axisLinePerpM']==.02 and lines[1]['momentDifferenceM']==.02
    assert lines[0]['axisLinePerpM']==lines[2]['axisLinePerpM']==0
    assert lines[1]['axisLinePerpM']>1e-5  # Legacy rest+direction passes; new line-position test rejects.

    records={}
    def memory_publisher(directory,value):
        # Original r3 publisher is not replaced in the adapter. This checks JSON data only.
        payload=json.dumps(value,allow_nan=False,sort_keys=True)
        records[directory.name]=json.loads(payload)
        return hashlib.sha256(payload.encode()).hexdigest()+'.json'
    healthy={'status':'observed-static-registration-only','stage':{'scale':[1.,1.,1.]}}
    record_numeric_vector(healthy,'finite-local-pose-schema',identity,7)
    success=publish_terminal(Path('success-terminal'),healthy,memory_publisher)
    assert healthy['status']==records['success-terminal']['status']=='observed-static-registration-only'
    assert records['success-terminal']['stage']['scale']==[1.,1.,1.]
    assert success['terminalPublicationConfirmed'] and success['terminalPersistenceError'] is None
    assert success['originalError'] is success['observationValidationError'] is None
    assert records['success-terminal-persistence']==success
    # Schema rejection only: these are malformed data, never evaluated configurations.
    for malformed in ([0.,0.,float('nan'),0.,0.,0.,0.],[0.,0.,False,0.,0.,0.,0.]):
        invalid={'status':'running'}
        try:record_numeric_vector(invalid,'malformed-readback-schema',malformed,7)
        except (NonFiniteObservation,ValueError):pass
        else:raise AssertionError('Bad scalar readback could reach a kinematic caller')
        assert invalid['status']=='failed' and invalid['rawObservations']
    original=ValueError('original stage validation error')
    source={'status':'failed','stage':{'scale':[float('nan'),1.,1.]},
            'error':{'type':'ValueError','message':str(original)}}
    receipt=publish_terminal(Path('nan-terminal'),source,memory_publisher,original)
    saved=records['nan-terminal'];tag=saved['stage']['scale'][0]['__c0_nonfinite__']
    assert saved['status']=='failed' and tag['kind']=='NaN'
    assert math.isnan(source['stage']['scale'][0])  # No replacement of the original observed scalar.
    assert math.isnan(struct.unpack('>d',bytes.fromhex(tag['observedPythonFloat64Bits']))[0])
    assert saved['stage']['scale'][1:]==[1.,1.]
    assert receipt['originalError']=={'type':'ValueError','message':str(original)}
    assert receipt['terminalPublicationConfirmed'] and records['nan-terminal-persistence']==receipt

    sticky={'status':'running'}
    try:record_observation(sticky,'raw-stage-scale',[float('inf'),float('-inf')])
    except NonFiniteObservation:pass
    else:raise AssertionError('Nonfinite observations were not rejected')
    sticky['status']='diagnostic-complete'  # A later label cannot erase the captured invalidity.
    try:publish_terminal(Path('sticky-terminal'),sticky,memory_publisher)
    except NonFiniteObservation:pass
    else:raise AssertionError('Invalid observation became green')
    assert records['sticky-terminal']['status']=='failed'
    assert [item['__c0_nonfinite__']['kind'] for item in
            records['sticky-terminal']['rawObservations'][0]['value']]==['+Infinity','-Infinity']

    def failed_terminal(directory,value):
        if directory.name=='failure-terminal':raise OSError('planned terminal publication failure')
        return memory_publisher(directory,value)
    try:publish_terminal(Path('failure-terminal'),source,failed_terminal,original)
    except OSError as error:assert error.__cause__ is original
    else:raise AssertionError('Publication failure was accepted')
    failed=records['failure-terminal-persistence']
    assert failed['originalError']==receipt['originalError']
    assert failed['terminalPersistenceError']['type']=='OSError'
    assert not failed['terminalPublicationConfirmed'] and failed['terminalMayExistAfterError']
    def failed_receipt(directory,value):
        if directory.name=='receipt-only-terminal-persistence':
            raise OSError('planned independent receipt publication failure')
        return memory_publisher(directory,value)
    stderr=io.StringIO()
    finite_failure={'status':'failed','stage':{'scale':[1.,1.,1.]},
                    'error':{'type':'ValueError','message':str(original)}}
    with contextlib.redirect_stderr(stderr):
        try:publish_terminal(Path('receipt-only-terminal'),finite_failure,failed_receipt,original)
        except OSError as error:assert error.__cause__ is original
        else:raise AssertionError('Receipt-only publication failure was accepted')
    outcome=json.loads(stderr.getvalue().splitlines()[-1])
    assert outcome['terminalPublicationConfirmed'] and outcome['terminalPersistenceError'] is None
    assert outcome['receiptPersistenceError']['type']=='OSError'
    assert outcome['receiptFile'] is None
    assert outcome['originalError']==receipt['originalError']
    assert records['receipt-only-terminal']['status']=='failed'
    assert 'receipt-only-terminal-persistence' not in records
    assert not forbidden()
    return {'axisLineCounterexample':'rest/direction same; perpendicular20mm pivot rejected',
            'parallelAxisGaugeAccepted':True,'nonfiniteOriginalErrorAndRawTagsRetained':True,
            'laterGreenLabelRejected':True,'publicationErrorDistinctFromOriginal':True,
            'finiteSuccessPreserved':True,'malformedReadbackSchemaRejectedBeforeKinematicUse':True,
            'receiptOnlyFailureSeparatelyRetainedInStderr':True,
            'staticRestFactorCompositions':3,'staticAxisLineComparisons':3,
            'configurationInputs':0,'modelOrTeacherImports':0,'fkOrIkCalls':0,
            'newOutputFilesOrChildProcesses':0,'forbiddenImports':forbidden()}


if __name__=='__main__':
    print(json.dumps(run(),sort_keys=True))
