"""C0 observation/terminal encoding only; caller supplies the unchanged immutable publisher."""
import json
import math
import struct
import sys


class NonFiniteObservation(RuntimeError):
    pass


def _encode(value,path='$'):
    if isinstance(value,float) and not math.isfinite(value):
        kind='NaN' if math.isnan(value) else ('+Infinity' if value>0 else '-Infinity')
        return {'__c0_nonfinite__':{'kind':kind,'rawRepr':repr(value),
                                  'observedPythonFloat64Bits':struct.pack('>d',value).hex()}},[
            {'path':path,'kind':kind}]
    if value is None or isinstance(value,(str,bool,int,float)):
        return value,[]
    if isinstance(value,(list,tuple)):
        result=[];issues=[]
        for index,item in enumerate(value):
            encoded,found=_encode(item,f'{path}[{index}]');result.append(encoded);issues.extend(found)
        return result,issues
    if isinstance(value,dict) and all(isinstance(key,str) for key in value):
        result={};issues=[]
        for key,item in value.items():
            result[key],found=_encode(item,path+'.'+key);issues.extend(found)
        return result,issues
    typename=type(value).__module__+'.'+type(value).__qualname__
    return {'__c0_unsupported__':{'type':typename,'rawRepr':repr(value)}},[
        {'path':path,'kind':'unsupported JSON observation','type':typename}]


def _error(error):
    return None if error is None else {'type':type(error).__name__,'message':str(error)}


def record_observation(result,label,value):
    """Save a tagged copy before validation/derived arithmetic; return original finite data."""
    encoded,issues=_encode(value,label)
    result.setdefault('rawObservations',[]).append({'label':label,'value':encoded})
    if issues:
        result.setdefault('invalidObservations',[]).extend(issues)
        result['status']='failed'
        raise NonFiniteObservation('Invalid observation at '+label)
    return value


def record_numeric_vector(result,label,value,length):
    """Retain raw input first; reject shape/type/nonfinite data before kinematic use."""
    record_observation(result,label,value)
    valid=isinstance(value,(list,tuple)) and len(value)==length and all(
        isinstance(item,(int,float)) and not isinstance(item,bool) and math.isfinite(item)
        for item in value)
    if not valid:
        result.setdefault('invalidObservations',[]).append(
            {'path':label,'kind':'numeric vector shape/type','expectedLength':length,
             'actualType':type(value).__name__})
        result['status']='failed'
        raise ValueError('Expected finite numeric vector of length '+str(length)+' at '+label)
    return value


def publish_terminal(directory,result,publisher,primary_error=None):
    payload,issues=_encode(result)
    issues=list(result.get('invalidObservations',[]))+issues
    validation_error=NonFiniteObservation('Invalid observations preserved in failed terminal') if issues else None
    if issues:
        payload['statusBeforeTerminalSafety']=payload.get('status')
        payload['status']=result['status']='failed'
    payload['terminalSafety']={'invalidObservationPaths':issues,'originalError':_error(primary_error),
                               'observationValidationError':_error(validation_error)}
    receipt={'terminalDirectory':str(directory),'terminalStatus':payload.get('status'),
             'originalError':_error(primary_error),'invalidObservationPaths':issues,
             'observationValidationError':_error(validation_error),
             'terminalPublicationConfirmed':False,'terminalMayExistAfterError':False,
             'terminalFile':None,'terminalPersistenceError':None}
    terminal_error=receipt_error=None
    try:
        name=publisher(directory,payload)
        assert isinstance(name,str),'Publisher did not return a closed published entry'
        receipt.update(terminalPublicationConfirmed=True,terminalFile=name)
    except BaseException as error:
        terminal_error=error;receipt['terminalPersistenceError']=_error(error)
        receipt['terminalMayExistAfterError']=True
    receipt_directory=directory.parent/(directory.name+'-persistence')
    try:
        receipt_file=publisher(receipt_directory,receipt)
        assert isinstance(receipt_file,str)
    except BaseException as error:
        receipt_error=error;receipt_file=None
    # A second publication failure cannot erase the original operation/error receipt from stderr.
    event=receipt|{'event':'C0 terminal publication outcome','receiptFile':receipt_file,
                   'receiptPersistenceError':_error(receipt_error)}
    sys.stderr.write(json.dumps(event,allow_nan=False,sort_keys=True)+'\n')
    if terminal_error is not None:raise terminal_error from primary_error
    if receipt_error is not None:raise receipt_error from primary_error
    if issues and primary_error is None:
        raise validation_error
    return receipt
