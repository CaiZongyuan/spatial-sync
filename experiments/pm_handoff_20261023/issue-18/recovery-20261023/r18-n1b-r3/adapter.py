"""Private R18-N1b method revision: two actual Teacher calls require a later numerical GO."""
import time
ENTRY_MONO=time.monotonic()
ENTRY_UTC=time.time()

import argparse
import contextlib
import hashlib
import importlib.machinery
import importlib.metadata
import json
import math
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
PRIMARY=HERE.parents[4]


class StageDeadline(RuntimeError):
    pass


class StageInterrupted(RuntimeError):
    pass


class InputDrift(RuntimeError):
    pass


class Budget:
    """One monotonic total bound; expiry is sticky, including during final verification."""
    def __init__(self,total=180,work=120,*,clock=time.monotonic,start=None,end=None):
        self.clock=clock
        self.start=clock() if start is None else start
        self.end=min(self.start+total,end) if end is not None else self.start+total
        self.work_end=min(self.start+work,self.end-60) if total==180 else min(self.start+work,self.end)
        self.fatal=False
        self.expirations=[]

    def check(self,phase='work'):
        now=self.clock()
        limit=self.work_end if phase=='work' else self.end
        if (phase=='work' and self.fatal) or now>=limit:
            self.fatal=True
            self.expirations.append({'phase':phase,'elapsed':now-self.start})
            raise StageDeadline('Monotonic '+phase+' allocation expired')
        return limit-now

    def finish(self):
        self.check('total')
        if self.fatal:
            raise StageDeadline('Earlier deadline is fatal even if a clock later moves backwards')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)


def publish_owner(directory,value):
    """Closed/fsynced immutable entry; recovery readers consume only published *.json."""
    directory.mkdir(exist_ok=True)
    payload=(json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode('utf-8')
    key=hashlib.sha256(payload).hexdigest()
    target=directory/(key+'.json')
    if target.exists():
        assert target.read_bytes()==payload
        return target.name  # Never rewrite an owner record a reader may have open.
    pending=directory/(key+'.pending')
    with pending.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(target)  # Fresh final name, not replacement of a concurrently read record.
    return target.name


def digest(path,budget,phase='work'):
    value=hashlib.sha256()
    budget.check(phase)
    with Path(path).open('rb') as stream:
        while raw:=stream.read(65536):
            budget.check(phase)
            value.update(raw)
    budget.check(phase)
    return value.hexdigest()


def verify_files(entries,snapshot,output,budget,phase):
    """All declared inputs, not the historical mechanical factory's unrelated33 files."""
    receipt={'phase':phase,'entries':[],'status':'running'}
    save(output,receipt)
    try:
        for entry in entries:
            actual=digest(entry['path'],budget,phase)
            row=entry | {'actualSha256':actual,'matches':actual==entry['sha256']}
            receipt['entries'].append(row)
            save(output,receipt)
            if not row['matches']:
                raise InputDrift(entry['role']+': '+str(entry['path']))
        budget.check(phase)
        receipt['git']=snapshot()
        save(output,receipt)
        if not receipt['git']['matches']:
            raise InputDrift('Head/tree/clean drift')
        ledger=next((entry for entry in entries if entry['role']=='old-ledger'),None)
        if ledger:
            old=load(ledger['path'])
            assert old['maximum']==2 and len(old['windows'])==2
            assert [v['number'] for v in old['windows']]==[1,2]
            receipt['oldLedger']='2/2 unchanged'
        budget.check(phase)
        receipt['status']='verified'
    except BaseException as error:
        receipt['status']='failed'
        receipt['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        save(output,receipt)
    return receipt


@contextlib.contextmanager
def catch_interrupts(record,*,defer=False):
    previous={}
    raised=False
    def handler(number,_frame):
        nonlocal raised
        record({'signal':number,'atMonotonic':time.monotonic()})
        if defer or raised:
            return
        raised=True
        raise StageInterrupted('Catchable signal '+str(number))
    try:
        for name in ('SIGINT','SIGTERM','SIGBREAK'):
            if hasattr(signal,name):
                number=getattr(signal,name)
                previous[number]=signal.signal(number,handler)
        yield
    finally:
        for number,handler in previous.items():
            signal.signal(number,handler)


def identity(proc):
    return {'pid':proc.pid,'created':proc.create_time(),'command':proc.cmdline()}


def observe(row,psutil):
    try:
        current=identity(psutil.Process(row['pid']))
        return {'recorded':row,'current':current,
                'state':'matching' if current==row else 'uncertain'}
    except psutil.NoSuchProcess:
        return {'recorded':row,'state':'gone'}
    except psutil.AccessDenied as error:
        return {'recorded':row,'state':'uncertain','error':repr(error)}


def signal_recorded(row,psutil,receipt):
    first=observe(row,psutil)
    if first['state']=='uncertain':
        receipt['uncertain'].append(first)
        return None
    if first['state']=='matching' and observe(row,psutil)['state']=='matching':
        process=psutil.Process(row['pid'])
        process.terminate()
        return process
    return None


def owned_call(argv,cwd,env,out,budget,*,phase='work'):
    """One task-owned command; immediate durable owner/inventory receipts and bounded cleanup."""
    import psutil
    out=Path(out)
    out.mkdir(parents=True)
    receipt={'argv':list(map(str,argv)),'cwd':str(cwd),'status':'starting',
             'processes':[],'uncertain':[],'cleanupErrors':[],'persistenceErrors':[]}
    path=out/'receipt.json'
    save(path,receipt)  # Exists before Popen; remains useful if startup is interrupted.
    process=root=None
    children={}

    def owner(value,*,strict=True):
        try:
            return publish_owner(out/'owners',value)
        except BaseException as error:
            receipt['persistenceErrors'].append({'operation':'immutable-owner','error':repr(error)})
            receipt['status']='failed'
            if strict:
                raise

    def persist(*,strict=True):
        receipt['processes']=([root] if root else [])+list(children.values())
        try:
            save(path,receipt)
        except BaseException as error:
            receipt['persistenceErrors'].append({'operation':'receipt-snapshot','error':repr(error)})
            receipt['status']='failed'
            if strict:
                raise

    def inventory(*,strict=True):
        nonlocal root
        if root is None and process is not None and process.poll() is None:
            # Persistence may fail before stable registration; current creating handle still owns cleanup.
            row=identity(psutil.Process(process.pid))
            if row['command'] and process.poll() is None and identity(psutil.Process(process.pid))==row:
                root=row
                owner({'kind':'verified-root','identity':row},strict=strict)
        if root is None or observe(root,psutil)['state']!='matching':
            return
        try:
            descendants=psutil.Process(root['pid']).children(recursive=True)
        except psutil.NoSuchProcess:
            return
        changed=False
        for child in descendants:
            try:
                row=identity(child)
                key=json.dumps(row,sort_keys=True)
                if row['command'] and key not in children and observe(root,psutil)['state']=='matching':
                    children[key]=row
                    changed=True
                    owner({'kind':'verified-descendant','identity':row},strict=strict)
            except psutil.NoSuchProcess:
                pass
        if changed:
            persist(strict=strict)

    try:
        budget.check(phase)
        options=({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt'
                 else {'start_new_session':True})
        with (out/'output.log').open('w',encoding='utf-8') as log:
            owner({'kind':'launch-intent','argv':receipt['argv'],'cwd':str(cwd)})
            process=subprocess.Popen(receipt['argv'],cwd=cwd,env=env,stdout=log,
                                     stderr=subprocess.STDOUT,**options)
            receipt['creatingHandlePid']=process.pid
            owner({'kind':'creating-handle','pid':process.pid,'argv':receipt['argv']})
            until=time.monotonic()+1
            previous=None
            while time.monotonic()<until:
                budget.check(phase)
                assert process.poll() is None,'Exited before stable owner registration'
                row=identity(psutil.Process(process.pid))
                if row['command'] and row==previous:
                    root=row
                    break
                previous=row
                time.sleep(.01)
            assert root is not None
            owner({'kind':'verified-root','identity':root})
            receipt['status']='running'
            persist()
            while process.poll() is None:
                budget.check(phase)
                state=observe(root,psutil)
                if state['state']=='gone':
                    process.wait(timeout=min(1,budget.check(phase)))
                    break
                if state['state']=='uncertain':
                    receipt['uncertain'].append(state)
                    persist()
                    raise RuntimeError('Owner identity uncertain')
                inventory()
                time.sleep(.02)
            assert process.returncode==0,'Owned command returned nonzero'
            receipt['status']='complete'
    except BaseException as error:
        receipt['status']='failed'
        receipt['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        def deferred_signal(event):
            receipt.setdefault('cleanupSignals',[]).append(event)
            persist(strict=False)
        with catch_interrupts(deferred_signal,defer=True):
            try:
                inventory(strict=False)
            except BaseException as error:
                receipt['cleanupErrors'].append('inventory: '+repr(error))
            persist(strict=False)
            for row in reversed(list(children.values())):
                try:
                    child=signal_recorded(row,psutil,receipt)
                    persist(strict=False)
                    if child:
                        try:
                            child.wait(timeout=min(1.5,max(.01,budget.end-budget.clock())))
                        except psutil.TimeoutExpired:
                            if observe(row,psutil)['state']=='matching':
                                child.kill()
                                child.wait(timeout=min(1,max(.01,budget.end-budget.clock())))
                except psutil.NoSuchProcess:
                    pass
                except BaseException as error:
                    receipt['cleanupErrors'].append(repr(error))
            if process is not None and process.poll() is None:
                try:
                    process.terminate()  # Current creating handle, not a searched PID.
                    try:
                        process.wait(timeout=min(2,max(.01,budget.end-budget.clock())))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=min(1,max(.01,budget.end-budget.clock())))
                except BaseException as error:
                    receipt['cleanupErrors'].append(repr(error))
            receipt['exitCode']=process.poll() if process else None
            receipt['postrun']=[observe(row,psutil) for row in ([root] if root else [])+list(children.values())]
            try:
                budget.check('total')
            except StageDeadline as error:
                receipt['deadlineError']=str(error)
            if ((phase=='work' and budget.fatal) or receipt['uncertain'] or receipt['cleanupErrors'] or receipt['persistenceErrors']
                    or receipt.get('cleanupSignals') or any(v['state']!='gone' for v in receipt['postrun'])):
                receipt['status']='failed'
            receipt['deadlineFatalSeen']=budget.fatal
            receipt['finishedMonotonic']=budget.clock()
            persist(strict=False)  # Failure is fatal to status, never to handle cleanup above.
            owner({'kind':'final-outcome','status':receipt['status'],
                   'processes':receipt['processes'],'postrun':receipt['postrun'],
                   'persistenceErrors':receipt['persistenceErrors'],
                   'uncertain':receipt['uncertain'],'cleanupErrors':receipt['cleanupErrors']},strict=False)
    assert receipt['status']=='complete'
    return (out/'output.log').read_text(encoding='utf-8')


def instrumented_solve(teacher,optimize,position,initial,orientation,TeachingError,budget):
    original_optimizer,opt_original_fk=optimize.least_squares,teacher.fk
    observed={'optimizerCalls':0,'fkCalls':0}
    captured=[]
    def captured_optimizer(*args,**kwargs):
        observed['optimizerCalls']+=1
        result=original_optimizer(*args,**kwargs)
        captured.append(result)
        return result
    def counted_fk(*args,**kwargs):
        budget.check('work')
        observed['fkCalls']+=1
        return opt_original_fk(*args,**kwargs)
    optimize.least_squares,teacher.fk=captured_optimizer,counted_fk
    try:
        accepted=teacher.solve(position,list(initial),orientation=orientation)
        outcome={'numericalPassed':True,'acceptedJoints':accepted,'strictGatePassedByActualTeacher':True}
    except TeachingError as error:
        outcome={'numericalPassed':False,'measurements':error.measurements}
    finally:
        optimize.least_squares,teacher.fk=original_optimizer,opt_original_fk
    assert observed['optimizerCalls']==1 and len(captured)==1
    result=captured[0]
    outcome.update(finalJoints=result.x.tolist(),optimizerStatus=int(result.status),nfev=int(result.nfev),
                   optimizerFinalAugmentedResidual=result.fun.tolist(),
                   optimizerCachedMeasurements={'positionErrorM':math.hypot(*result.fun[:3]),
                    'angleErrorRad':math.hypot(*result.fun[3:6])/.2,
                    'weightedResidual':math.hypot(*result.fun[:6])},**observed)
    assert outcome['nfev']<=400
    return outcome


def solve_cells(plan,teacher_factory,optimize,error_type,budget,record):
    results=[]
    for cell in plan['cells']:
        budget.check('work')
        teacher=teacher_factory()
        row={'cell':cell['name'],'seed':list(plan['fixedSeed']),'status':'running'}
        record(row)
        row.update(instrumented_solve(teacher,optimize,plan['targetWorldM'],plan['fixedSeed'],
                   cell['orientation'],error_type,budget))
        row['status']='numerical-pass' if row['numericalPassed'] else 'numerical-fail'
        results.append(row)
        record(row)
    assert len(results)==2 and sum(v['nfev'] for v in results)<=800
    budget.check('work')
    return results


def declared_entries(plan,repo,authorization,auth_path):
    entries=[{'role':'plan','path':str(HERE/'STAGE_PLAN.json'),'sha256':authorization['planSha256']},
             {'role':'adapter','path':str(Path(__file__).resolve()),'sha256':authorization['adapterSha256']},
             {'role':'authorization','path':str(auth_path),'sha256':authorization['_entrySha256']}]
    entries.extend({'role':'source','path':str(repo/name),'sha256':sha} for name,sha in plan['sourceHashes'].items())
    entries.extend({'role':'original-input','path':str(PRIMARY/name),'sha256':sha} for name,sha in plan['originalInputHashes'].items())
    entries.append(plan['oldLedger'])
    return entries


def environment(plan,repo,budget,phase='work'):
    budget.check(phase)
    assert os.name=='nt' and sys.platform=='win32','Windows-only numerical stage'
    assert sys.version_info[:2]==(3,13)
    assert Path(sys.prefix).resolve()==repo/'.venv'
    assert Path(sys.executable).resolve().is_relative_to(repo/'.venv')
    config=(repo/'.venv/pyvenv.cfg').read_text().lower()
    assert 'include-system-site-packages = false' in config
    git=Path(plan['windowsGit']['path'])
    assert git.suffix.lower()=='.exe' and git.is_file()
    site=repo/'.venv/Lib/site-packages'
    observed={}
    for package,requirement in plan['dependencies'].items():
        budget.check(phase)
        distribution=importlib.metadata.distribution(package)
        assert distribution.version==requirement['version']
        assert Path(distribution.locate_file('')).resolve().is_relative_to(site)
        spec=importlib.machinery.PathFinder.find_spec(requirement['module'],[str(site)])
        assert spec and Path(spec.origin).resolve().is_relative_to(site)
        observed[package]={'version':distribution.version,'originWithoutImport':spec.origin,
                           'originSha256':digest(spec.origin,budget,phase)}
    return {'platform':sys.platform,'prefix':sys.prefix,'executable':sys.executable,
            'git':str(git),'dependencies':observed}


def authorization(args,budget):
    budget.check('work')
    plan=load(HERE/'STAGE_PLAN.json')
    auth=load(args.authorization)
    auth['_entrySha256']=digest(args.authorization,budget)
    assert auth['scope']=='R18-N1b-offline-slot3-two-cell' and auth['pmGo']==args.pm_go
    assert auth['numericalBudgetAllocated'] is True
    assert auth['planSha256']==digest(HERE/'STAGE_PLAN.json',budget)
    assert auth['adapterSha256']==digest(__file__,budget) and auth['head']==plan['head']
    assert [v['name'] for v in plan['cells']]==['A','B']
    repo=args.repo.resolve()
    assert repo.is_relative_to(PRIMARY/'.worktrees') and repo!=PRIMARY/'.worktrees'
    return plan,auth,repo


def main(args):
    out=args.output.resolve()
    assert out.is_relative_to(HERE)
    if args.worker:
        auth_initial=load(args.authorization)
        utc_end=datetime.fromisoformat(auth_initial['deadlineUtc'].replace('Z','+00:00')).timestamp()
        budget=Budget(start=ENTRY_MONO,end=min(args.total_end,ENTRY_MONO+utc_end-ENTRY_UTC))
        plan,auth,repo=authorization(args,budget)
        # A worker inherits the parent's same-system monotonic total/work bounds.
        budget.end=min(budget.end,args.total_end)
        budget.work_end=min(budget.work_end,args.work_end)
        environment(plan,repo,budget)
        budget.check('work')
        sys.path.insert(0,str(repo))
        import scipy.optimize as optimize
        import tools.teach as actual
        assert Path(actual.__file__).resolve()==repo/'tools/teach.py'
        assert not any(name in sys.modules for name in ('newton','warp','pxr.Usd'))
        loaded={name:str(Path(module.__file__).resolve()) for name,module in list(sys.modules.items())
                if name in ('numpy','scipy','scipy.optimize','httpx','yaml','pydantic')}
        assert all(Path(value).is_relative_to(repo/'.venv/Lib/site-packages') for value in loaded.values())
        save(out/'worker-import-origins.json',loaded)
        rows=solve_cells(plan,lambda:actual.Teacher(asset=repo/plan['urdfRelative'],base=plan['baseMatrix']),
                         optimize,actual.TeachingError,budget,
                         lambda row:save(out/('cell-'+row['cell']+'.json'),row))
        after={name:str(Path(module.__file__).resolve()) for name,module in list(sys.modules.items())
               if name in ('numpy','scipy','scipy.optimize','scipy.spatial.transform',
                           'scipy.spatial.transform._rotation','httpx','yaml','pydantic')}
        assert all(Path(value).is_relative_to(repo/'.venv/Lib/site-packages') for value in after.values())
        save(out/'worker-import-origins-after.json',after)
        save(out/'worker.json',{'status':'diagnostic-complete','cells':rows,'newtonP6Verified':False})
        return
    assert not out.exists()
    out.mkdir(parents=True)
    budget=Budget(start=ENTRY_MONO)
    state={'status':'running','stage':'R18-N1b','startMonotonic':budget.start,
           'totalEndMonotonic':budget.end,'workEndMonotonic':budget.work_end,'errors':[],
           'supervisorPid':os.getpid(),'argv':sys.argv}
    save(out/'stage.json',state)
    def interrupt(event):
        state['interrupt']=event
        save(out/'stage.json',state)
    entries=None
    def snapshot(phase):
        git=plan['windowsGit']['path']
        head=owned_call([git,'rev-parse','HEAD'],repo,os.environ,out/(phase+'-git-head'),budget,phase=phase).strip()
        tree=owned_call([git,'show','-s','--format=%T','HEAD'],repo,os.environ,out/(phase+'-git-tree'),budget,phase=phase).strip()
        clean=owned_call([git,'status','--porcelain'],repo,os.environ,out/(phase+'-git-clean'),budget,phase=phase).strip()
        return {'expectedHead':plan['head'],'head':head,'expectedTree':plan['tree'],'tree':tree,'clean':clean,
                'matches':head==plan['head'] and tree==plan['tree'] and not clean}
    with catch_interrupts(interrupt):
        try:
            auth_initial=load(args.authorization)
            utc_end=datetime.fromisoformat(auth_initial['deadlineUtc'].replace('Z','+00:00')).timestamp()
            budget.end=min(budget.end,ENTRY_MONO+utc_end-ENTRY_UTC)
            budget.work_end=min(budget.work_end,budget.end-60)
            plan,auth,repo=authorization(args,budget)
            entries=declared_entries(plan,repo,auth,args.authorization.resolve())
            entries.append({'role':'windows-git','path':plan['windowsGit']['path'],'sha256':plan['windowsGit']['sha256']})
            state['environment']=environment(plan,repo,budget)
            import psutil
            state['supervisorIdentity']=identity(psutil.Process())
            state.update(totalEndMonotonic=budget.end,workEndMonotonic=budget.work_end)
            save(out/'stage.json',state)
            state['pre']=verify_files(entries,lambda:snapshot('work'),out/'verification-pre.json',budget,'work')
            env=os.environ | {'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                 'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
            argv=[sys.executable,'-I',str(Path(__file__).resolve()),'--worker','--repo',str(repo),
                  '--authorization',str(args.authorization.resolve()),'--pm-go',args.pm_go,'--output',str(out),
                  '--total-end',str(budget.end),'--work-end',str(budget.work_end)]
            owned_call(argv,repo,env,out/'owned-worker',budget)
            assert load(out/'worker.json')['status']=='diagnostic-complete'
        except BaseException as error:
            state['errors'].append({'type':type(error).__name__,'message':str(error)})
        finally:
            try:
                assert entries is not None,'Preflight did not reach verified declared input identities'
                state['post']=verify_files(entries,lambda:snapshot('total'),out/'verification-post.json',budget,'total')
                state['environmentPost']=environment(plan,repo,budget,'total')
                assert state['environmentPost']==state['environment'],'Dependency origin/version drift'
                budget.finish()
            except BaseException as error:
                state['errors'].append({'type':type(error).__name__,'message':str(error)})
            state.update(status='failed' if state['errors'] or budget.fatal else 'diagnostic-complete',
                         wallSeconds=budget.clock()-budget.start,deadlineFatal=budget.fatal,
                         deadlineEvents=budget.expirations,finishedUtc=datetime.now(timezone.utc).isoformat())
            save(out/'stage.json',state)
            try:
                budget.finish()  # A slow final write cannot retain a green result beyond total180.
            except StageDeadline as error:
                state.update(status='failed',deadlineFatal=True,finalWriteDeadlineError=str(error))
                save(out/'stage.json',state)
    assert state['status']=='diagnostic-complete'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--authorization',type=Path,required=True)
    parser.add_argument('--pm-go',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--total-end',type=float,help=argparse.SUPPRESS)
    parser.add_argument('--work-end',type=float,help=argparse.SUPPRESS)
    main(parser.parse_args())
