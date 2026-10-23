"""UNALLOCATED C0 proposal. Fresh model/Teacher children; no Source or dynamics."""
import time
ENTRY=time.monotonic()
ENTRY_UTC=time.time()
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import sys
from datetime import datetime,timezone
from pathlib import Path

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))  # -I excludes the script directory; only this task-local data helper is added.
plan=json.loads((HERE/'PLAN.json').read_text(encoding='utf-8'))
spec=importlib.util.spec_from_file_location('unchanged_r3_ownership',plan['r3Adapter'])
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from pose_data import axis_line_error,compose,inverse,matrix_error,pose_matrix,rebase_prediction,rotate,urdf_chain
from terminal_data import publish_terminal,record_numeric_vector,record_observation


def check_authorization(args,budget):
    budget.check()
    auth=m.load(args.authorization)
    assert auth['scope']=='R18-C0-static-paired-registration' and auth['pmGo']==args.pm_go
    assert auth['modelBudgetAllocated'] is True
    assert m.digest(HERE/'PLAN.json',budget)==auth['planSha256']
    assert m.digest(__file__,budget)==auth['adapterSha256']
    assert m.digest(HERE/'SOURCE_MANIFEST.json',budget)==auth['sourceManifestSha256']
    assert m.digest(plan['r3Adapter'],budget)==plan['r3AdapterSha256']
    end=datetime.fromisoformat(auth['deadlineUtc'].replace('Z','+00:00')).timestamp()
    budget.end=min(budget.end,ENTRY+end-ENTRY_UTC)
    budget.work_end=min(budget.work_end,budget.end-60)
    if args.total_end is not None:
        budget.end=min(budget.end,args.total_end)
        budget.work_end=min(budget.work_end,args.work_end)
    budget.check()
    return auth


def origins(expected_repo,expected_venv):
    product=[]
    for name,module in sorted(sys.modules.items()):
        filename=getattr(module,'__file__',None)
        if not filename:continue
        path=Path(filename).resolve()
        if name.startswith(('packages.','apps.','tools.')):
            assert path.is_relative_to(expected_repo),(name,str(path))
            product.append({'name':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        elif name.split('.')[0] in ('newton','warp','pxr','numpy','scipy','yaml','pydantic','httpx','psutil'):
            assert path.is_relative_to(expected_venv),(name,str(path))
    return product


def environment(kind):
    repo=Path(plan['runtimeRepo'] if kind=='model' else plan['teacherRepo']).resolve()
    executable=Path(plan['modelPython'] if kind=='model' else plan['teacherPython']).resolve()
    assert Path(sys.executable).resolve()==executable
    assert sys.version_info[:2]==(3,13)
    expected=json.loads((HERE/'ENVIRONMENT_PINS.json').read_text(encoding='utf-8'))[kind]
    site=Path(sys.prefix)/('Lib/site-packages' if os.name=='nt' else 'lib/python3.13/site-packages')
    actual={}
    for name,version in expected['versions'].items():
        distribution=importlib.metadata.distribution(name)
        assert distribution.version==version
        assert Path(distribution.locate_file('')).resolve().is_relative_to(site)
        actual[name]={'version':version,'root':str(Path(distribution.locate_file('')).resolve())}
    return repo,Path(sys.prefix).resolve(),actual


def model_worker(args,budget):
    repo,venv,versions=environment('model')
    sys.path.insert(0,str(repo))
    out=args.output
    counters={'modelAttempts':0,'modelFinalizeEntered':0,'modelsCompleted':0,'constructionEvalFkEntered':0,
              'staticEvalFkEntered':0,'staticEvalFkCompleted':0,'urdfDescriptorPasses':0,
              'staticAxisLineComparisons':0,'additionalConfigurationFkPasses':0,'forbiddenCalls':[]}
    phase='construction'
    result={'status':'running','counters':counters,'environment':versions}
    previous_profile=sys.getprofile()
    primary_error=None
    def profile(frame,event,_arg):
        if event!='call':return
        module=frame.f_globals.get('__name__','');name=frame.f_code.co_name
        if module=='newton._src.sim.builder' and name=='finalize':
            counters['modelFinalizeEntered']+=1
            if counters['modelFinalizeEntered']>1:
                raise RuntimeError('More than one actual ModelBuilder.finalize entry')
        if module=='newton._src.sim.articulation' and name=='eval_fk':
            budget.check()
            key='staticEvalFkEntered' if phase=='static' else 'constructionEvalFkEntered'
            counters[key]+=1
            if phase!='static' or counters[key]>2:
                raise RuntimeError('Unexpected implicit/excess Newton eval_fk; stop before extra work')
        instance=frame.f_locals.get('self')
        typename=type(instance).__name__ if instance is not None else ''
        forbidden=(name=='__init__' and (typename in {'NewtonSimulation','NewtonInfoHost','CollisionPipeline'}
                   or typename.endswith('Source') and module.startswith('apps.api.app.sources')
                   or typename.startswith('Solver') and module.startswith('newton.')))
        forbidden |= name in {'step','collide','eval_ik'} and module.startswith(('newton.','packages.sim_runtime.'))
        if forbidden:
            counters['forbiddenCalls'].append(module+'.'+frame.f_code.co_qualname)
            raise RuntimeError('Forbidden dynamics/Source/solver operation')
    sys.setprofile(profile)
    try:
        import newton
        import warp as wp
        from pxr import Gf,Usd,UsdGeom
        from apps.api.app.library_validation import program_schemas
        from packages.library.common import content_hash
        from packages.library.metadata import asset_metadata,scene_metadata
        from packages.library.structural import USDGraph
        from packages.library.validation import StructuralValidator
        from packages.protocol.math import compose_pose
        from packages.sim_runtime.library_scene import prepare_scene_config
        assert newton.eval_fk.__module__=='newton._src.sim.articulation'
        wp.config.kernel_cache_dir=str(out/'kernel-cache')
        scene=Path(plan['scene']['path'])
        assert content_hash(scene)==plan['scene']['contentHash']
        metadata,bindings,locations=scene_metadata(scene)
        static=m.load(plan['staticValidationPath'])
        assets=[]
        for ref in plan['assets']:
            budget.check()
            path=Path(ref['path'])
            actual={p.relative_to(path).as_posix():p.stat().st_size for p in path.rglob('*') if p.is_file()}
            assert actual=={f['name']:f['bytes'] for f in ref['files']},ref['id']
            assets.append(StructuralValidator.scope(path,identifier=ref['id'],
                metadata=asset_metadata(path),digest=ref['contentHash']))
        payload={'kind':'scene','subject':'scene:'+plan['scene']['id'],'metadata':metadata,
            'bindings':bindings,'locations':locations,
            'package':StructuralValidator.scope(scene,identifier=plan['scene']['id'],metadata=metadata,
                digest=plan['scene']['contentHash']),
            'assets':assets,'expectedAssets':[{'id':a['id'],'contentHash':a['contentHash']} for a in plan['assets']],
            'programs':program_schemas(),'structure':static['scene']}
        # Observe the actual composed stage; do not alter any hash/resolve/verified guard.
        original_compose=USDGraph.compose
        captured={}
        def capture_graph(graph,*values,**kwargs):
            stage=original_compose(graph,*values,**kwargs)
            captured.update(graph=graph,stage=stage)
            return stage
        USDGraph.compose=capture_graph
        try:
            config=prepare_scene_config(payload,include_visuals=False)
        finally:
            USDGraph.compose=original_compose
        graph,stage=captured['graph'],captured['stage']
        assert stage is not None and not graph.errors
        assert graph.referenced=={a['id'] for a in plan['assets']}
        assert graph.verified==graph.referenced
        root=stage.GetPrimAtPath(plan['robotKey'])
        transform=UsdGeom.Xformable(root).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        matrix_rows=[list(map(float,row)) for row in transform]
        record_observation(result,'robotWorldMatrixUsdRowConvention',matrix_rows)
        scale=list(map(float,Gf.Transform(transform).GetScale()))
        record_observation(result,'robotWorldScale',scale)
        stage_registration={'metersPerUnit':UsdGeom.GetStageMetersPerUnit(stage),
            'upAxis':str(UsdGeom.GetStageUpAxis(stage)),
            'robotWorldMatrixUsdRowConvention':matrix_rows,'robotWorldScale':scale,
            'verifiedRuntimeAssetIds':sorted(graph.verified)}
        record_observation(result,'stageRegistration',stage_registration)
        result['stageRegistration']=stage_registration
        assert result['stageRegistration']['metersPerUnit']==1 and result['stageRegistration']['upAxis']=='Z'
        assert all(abs(value-1)<1e-12 for value in result['stageRegistration']['robotWorldScale'])
        budget.check();counters['modelAttempts']+=1
        builder=config.builder()
        model=builder.finalize(device='cpu');counters['modelsCompleted']+=1
        assert model.device.is_cpu and counters['constructionEvalFkEntered']==0
        assert counters['modelsCompleted']==counters['modelFinalizeEntered']==1
        state=model.state()
        labels=list(model.joint_label);bodies=list(model.body_label)
        qstarts=model.joint_q_start.numpy();dstarts=model.joint_qd_start.numpy()
        parents=model.joint_parent.numpy();children=model.joint_child.numpy()
        types=model.joint_type.numpy()
        xp=model.joint_X_p.numpy();xc=model.joint_X_c.numpy();axes=model.joint_axis.numpy()
        qindices=[];observed=[]
        for index,label in enumerate(labels):
            if not label.startswith(plan['robotKey']+'/'):continue
            first,last=int(qstarts[index]),int(qstarts[index+1])
            dfirst,dlast=int(dstarts[index]),int(dstarts[index+1])
            row={'name':label.rsplit('/',1)[-1],'label':label,
                'jointType':newton.JointType(int(types[index])).name.lower(),
                'parent':bodies[int(parents[index])] if int(parents[index])>=0 else 'world',
                'child':bodies[int(children[index])],
                'qStart':first,'qCount':last-first,'dofCount':dlast-dfirst,
                'parentJointPose':xp[index].tolist(),'childJointPose':xc[index].tolist(),
                'axesLocal':[axis.tolist() for axis in axes[dfirst:dlast]]}
            record_observation(result,'joint:'+label,row)
            row['parentChildRestPose']=compose(row['parentJointPose'],inverse(row['childJointPose']))
            observed.append(row)
        by_name={row['name']:row for row in observed}
        for key in plan['jointKeys']:
            row=by_name[key.rsplit('/',1)[-1]]
            assert row['qCount']==row['dofCount']==1 and row['jointType']=='revolute'
            qindices.append(row['qStart'])
        expected=urdf_chain(plan['urdf']);counters['urdfDescriptorPasses']+=1;mapping=[]
        for edge in expected:
            row=by_name.get(edge['name'])
            if edge['type']=='revolute':
                assert row is not None and row['parent'].endswith('/'+edge['parent']) and row['child'].endswith('/'+edge['child'])
                error=matrix_error(pose_matrix(row['parentChildRestPose']),pose_matrix(edge['originPose']))
                actual_axis=rotate(row['parentJointPose'][3:],row['axesLocal'][0])
                expected_axis=rotate(edge['originPose'][3:],edge['axis'])
                error.update(axis_line_error(row['parentJointPose'][:3],actual_axis,
                                             edge['originPose'][:3],expected_axis))
                error['axisLineParentFrame']=edge['parent']
                counters['staticAxisLineComparisons']+=1
                record_observation(result,'static-axis-line:'+edge['name'],error)
                mapping.append({'urdf':edge,'model':row,'kind':'one-to-one revolute','error':error})
            else:
                mapping.append({'urdf':edge,'model':row,'kind':'fixed mapping; folded checks recorded below'})
        # j8 and the hand yaw are folded into the actual fixed hand edge; TCP is a real Site.
        fold=compose(next(e['originPose'] for e in expected if e['name']=='fr3_joint8'),
                     next(e['originPose'] for e in expected if e['name']=='fr3_hand_joint'))
        hand_edge=by_name['fr3_hand_joint']
        assert hand_edge['parent'].endswith('/fr3_link7') and hand_edge['child']==plan['handBody']
        assert hand_edge['qCount']==hand_edge['dofCount']==0 and hand_edge['jointType']=='fixed'
        assert by_name['fr3_base_joint']['jointType']=='fixed'
        result['structure']={'expectedUrdfAncestors':expected,'observedRobotJoints':observed,'mapping':mapping,
            'foldedJ8HandExpectedPose':fold,'foldedJ8HandActualPose':hand_edge['parentChildRestPose'],
            'foldedJ8HandError':matrix_error(pose_matrix(fold),pose_matrix(hand_edge['parentChildRestPose']))}
        assert counters['staticAxisLineComparisons']==7 and counters['urdfDescriptorPasses']==1
        shape_labels=list(model.shape_label);tcp_index=shape_labels.index(plan['tcpSite'])
        assert shape_labels.count(plan['tcpSite'])==1
        owners=model.shape_body.numpy();locals_=model.shape_transform.numpy()
        flags=model.shape_flags.numpy()
        assert int(flags[tcp_index]) & int(newton.ShapeFlags.SITE)
        hand_index=bodies.index(plan['handBody']);base_index=bodies.index(plan['robotKey']+'/fr3_link0')
        assert int(owners[tcp_index])==hand_index
        tcp_local=locals_[tcp_index].tolist()
        record_numeric_vector(result,'tcp-local-before-pose-arithmetic',tcp_local,7)
        assert math.hypot(*tcp_local[3:])>0,'Actual TCP local quaternion is zero'
        result['structure']['tcpLocalPose']=tcp_local
        result['structure']['tcpLocalError']=matrix_error(pose_matrix(tcp_local),
            pose_matrix(next(e['originPose'] for e in expected if e['name']=='fr3_hand_tcp_joint')))
        result['samples']=[];phase='static'
        for sample in plan['samples']:
            budget.check();values=state.joint_q.numpy().copy()
            for index,value in zip(qindices,sample['q'],strict=True):values[index]=value
            state.joint_q.assign(values);state.joint_qd.zero_()
            before_fk=state.joint_q.numpy()[qindices].tolist()
            record_numeric_vector(result,'actual-readback-before-FK:'+sample['name'],before_fk,7)
            assert all(abs(a-b)<=4*2**-23*max(1,abs(b)) for a,b in zip(before_fk,sample['q'],strict=True))
            newton.eval_fk(model,state.joint_q,state.joint_qd,state)
            counters['staticEvalFkCompleted']+=1;budget.check()
            readback=state.joint_q.numpy()[qindices].tolist()
            record_numeric_vector(result,'actual-readback-after-FK:'+sample['name'],readback,7)
            assert readback==before_fk,'Static eval_fk unexpectedly changed its coordinate input'
            body=state.body_q.numpy();hand=body[hand_index].tolist();base=body[base_index].tolist()
            record_observation(result,'model-pose:'+sample['name'],
                {'actualReadbackQ':readback,'basePose':base,'handPose':hand,'tcpLocalPose':tcp_local})
            assert all(math.isfinite(v) for v in hand+base+tcp_local)
            tcp=compose_pose({'p':hand[:3],'q':hand[3:]},{'p':tcp_local[:3],'q':tcp_local[3:]})
            slots=[]
            for target in plan['slotTargets']:
                slot=shape_labels.index(target['site']);assert shape_labels.count(target['site'])==1
                assert int(flags[slot]) & int(newton.ShapeFlags.SITE)
                owner=int(owners[slot]);local=locals_[slot].tolist()
                assert owner>=0
                slot_body=body[owner].tolist()
                record_observation(result,'slot-pose:'+sample['name']+':'+target['site'],
                                   {'ownerPose':slot_body,'localPose':local})
                assert all(math.isfinite(v) for v in slot_body+local)
                world=compose_pose({'p':slot_body[:3],'q':slot_body[3:]},{'p':local[:3],'q':local[3:]})
                slots.append({'site':target['site'],'ownerBody':bodies[owner],
                              'actualWorldPose':[*world['p'],*world['q']],
                              'retainedBottomWorldM':target['bottomWorldM']})
            result['samples'].append({'name':sample['name'],'declaredQ':sample['q'],'jointKeys':plan['jointKeys'],
                'actualReadbackQ':readback,'basePose':base,'handPose':hand,'tcpPose':[*tcp['p'],*tcp['q']],
                'tcpRawLocalPose':tcp_local,'slotRegistrationOnly':slots})
        assert counters['staticEvalFkEntered']==counters['staticEvalFkCompleted']==2
        result['runtimeAssetsPost']=[]
        for asset in plan['assets']:
            budget.check('total')
            digest=content_hash(Path(asset['path']))
            result['runtimeAssetsPost'].append({'id':asset['id'],'contentHash':digest})
            assert digest==asset['contentHash'],asset['id']
        assert content_hash(scene)==plan['scene']['contentHash']
        result['productOrigins']=origins(repo,venv)
        assert not any(name.startswith('tools.teach') for name in sys.modules)
        result['status']='observed-static-registration-only'
    except BaseException as error:
        primary_error=error
        result['status']='failed';result['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        sys.setprofile(previous_profile)
        publish_terminal(out/'model-terminal',result,m.publish_owner,primary_error)


def teacher_worker(args,budget):
    repo,venv,versions=environment('teacher');sys.path.insert(0,str(repo))
    assert not any(name in sys.modules for name in ('newton','warp'))
    result={'status':'running','environment':versions,'fkCalls':0,'samples':[]}
    primary_error=None
    try:
        assert m.digest(args.model_result,budget)==args.model_sha
        model=m.load(args.model_result)
        assert model['status']=='observed-static-registration-only'
        assert len(model['samples'])==len(plan['samples'])==2
        for sample,declared in zip(model['samples'],plan['samples'],strict=True):
            assert sample['name']==declared['name'] and sample['jointKeys']==plan['jointKeys']
            assert sample['declaredQ']==declared['q']
            record_numeric_vector(result,'model-readback-before-Teacher:'+sample['name'],
                                  sample['actualReadbackQ'],7)
        from tools.teach import Teacher
        assert Path(sys.modules['tools.teach'].__file__).resolve()==repo/'tools/teach.py'
        teacher=Teacher(Path(plan['urdf']),base=plan['baseMatrix'])
        for sample in model['samples']:
            budget.check();result['fkCalls']+=1
            matrix=teacher.fk(list(sample['actualReadbackQ']))
            budget.check()
            record_observation(result,'Teacher-matrix:'+sample['name'],
                               {'sameInputQ':sample['actualReadbackQ'],'urdfWorldMatrix':matrix.tolist()})
            assert all(math.isfinite(float(v)) for row in matrix for v in row)
            result['samples'].append({'name':sample['name'],'sameInputQ':sample['actualReadbackQ'],
                                      'urdfWorldMatrix':matrix.tolist()})
        assert result['fkCalls']==2 and len(result['samples'])==2
        assert not any(name in sys.modules for name in ('newton','warp'))
        result['productOrigins']=origins(repo,venv)
        result['status']='two-exact-readback-Teacher-FK-only'
    except BaseException as error:
        primary_error=error
        result['status']='failed';result['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        publish_terminal(args.output/'teacher-terminal',result,m.publish_owner,primary_error)


def terminal(directory):
    files=list(directory.glob('*.json'));assert len(files)==1
    return files[0],m.load(files[0])


def main(args):
    assert not (args.model and args.teacher)
    budget=m.Budget(start=ENTRY)
    check_authorization(args,budget)
    args.output=args.output.resolve()
    assert args.output.is_relative_to(HERE)
    if args.model:return model_worker(args,budget)
    if args.teacher:return teacher_worker(args,budget)
    assert not args.output.exists();args.output.mkdir(parents=True)
    import psutil
    state={'status':'running','scope':'C0 static registration only','owner':m.identity(psutil.Process()),
        'startedUtc':datetime.now(timezone.utc).isoformat(),'startMonotonic':ENTRY,'endMonotonic':budget.end,
        'samples':[],'errors':[],'mechanicalBudget':'2/2 unchanged'}
    with (args.output/'OWNER.json').open('x',encoding='utf-8') as stream:json.dump(state,stream,indent=2)
    entries=m.load(HERE/'SOURCE_MANIFEST.json')['files']
    entries += [{'role':'authorization','path':str(args.authorization),'sha256':m.digest(args.authorization,budget)}]
    calls=0;env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'1',
        'OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    def command(argv,cwd,label,phase='work'):
        nonlocal calls
        calls+=1
        return m.owned_call(argv,cwd,env,args.output/(f'{calls:02d}-'+label),budget,phase=phase).strip()
    def snapshot(phase):
        rows=[]
        for kind,repo,head,tree in [('runtime',plan['runtimeRepo'],plan['runtimeHead'],plan['runtimeTree']),
                                   ('teacher',plan['teacherRepo'],plan['teacherHead'],plan['teacherTree'])]:
            actual_head=command([plan['windowsGit'],'rev-parse','HEAD'],repo,kind+'-head',phase)
            actual_tree=command([plan['windowsGit'],'show','-s','--format=%T','HEAD'],repo,kind+'-tree',phase)
            clean=command([plan['windowsGit'],'status','--porcelain'],repo,kind+'-clean',phase)
            rows.append({'repo':repo,'head':actual_head,'tree':actual_tree,'clean':clean,
                         'matches':actual_head==head and actual_tree==tree and not clean})
        return {'repositories':rows,'matches':all(row['matches'] for row in rows)}
    primary_error=None
    try:
        state['pre']=m.verify_files(entries,lambda:snapshot('work'),args.output/'pre.json',budget,'work')
        common=['-I','-B',str(Path(__file__).resolve()),'--authorization',str(args.authorization),
            '--pm-go',args.pm_go,'--output',str(args.output),'--total-end',str(budget.end),'--work-end',str(budget.work_end)]
        command([plan['modelPython'],*common,'--model'],plan['runtimeRepo'],'owned-model')
        model_path,model=terminal(args.output/'model-terminal')
        assert model['status']=='observed-static-registration-only'
        model_sha=m.digest(model_path,budget)
        entries.append({'role':'observed-model','path':str(model_path),'sha256':model_sha})
        command([plan['teacherPython'],*common,'--teacher','--model-result',str(model_path),
                 '--model-sha',model_sha],plan['teacherRepo'],'owned-teacher')
        _,teacher=terminal(args.output/'teacher-terminal')
        assert teacher['status']=='two-exact-readback-Teacher-FK-only'
        for observed,predicted in zip(model['samples'],teacher['samples'],strict=True):
            assert observed['name']==predicted['name'] and observed['actualReadbackQ']==predicted['sameInputQ']
            matrix=predicted['urdfWorldMatrix'];actual=pose_matrix(observed['tcpPose'])
            same_base=rebase_prediction(matrix,plan['baseMatrix'],observed['basePose'])
            comparison={'name':observed['name'],'sameQ':observed['actualReadbackQ'],
                'declaredBaseTcpError':matrix_error(actual,matrix),
                'sameObservedBaseTcpError':matrix_error(actual,same_base),
                'baseError':matrix_error(pose_matrix(observed['basePose']),plan['baseMatrix'])}
            record_observation(state,'paired-residual:'+observed['name'],comparison)
            state['samples'].append(comparison)
        state['model']=model;state['teacher']=teacher
        def within(error):
            return (error['positionM']<=plan['diagnosticPositionToleranceM']
                    and error['angleRad']<=plan['diagnosticAngleToleranceRad'])
        structural=[row['error'] for row in model['structure']['mapping'] if 'error' in row]
        structural += [model['structure']['foldedJ8HandError'],model['structure']['tcpLocalError']]
        structure_ok=all(within(error) and error.get('axisDifference',0)<=plan['diagnosticAxisTolerance']
                         and error.get('axisLinePerpM',0)<=plan['diagnosticPositionToleranceM']
                         for error in structural)
        state['diagnosticComparison']={'structureWithinDiagnosticTolerance':structure_ok,
            'sameObservedBasePosesWithinTolerance':all(within(row['sameObservedBaseTcpError']) for row in state['samples']),
            'declaredBasePosesWithinTolerance':all(within(row['declaredBaseTcpError']) and within(row['baseError'])
                                                for row in state['samples']),
            'notP6OrGlobalPhysicalFeasibility':True}
        state['status']='diagnostic-complete; not P6 or feasibility'
    except BaseException as error:
        primary_error=error
        state['status']='failed';state['errors'].append({'type':type(error).__name__,'message':str(error)})
    finally:
        try:
            state['post']=m.verify_files(entries,lambda:snapshot('total'),args.output/'post.json',budget,'total')
            budget.finish()
        except BaseException as error:
            if primary_error is None:primary_error=error
            state['status']='failed';state['errors'].append({'type':type(error).__name__,'message':str(error)})
        state.update(finishedUtc=datetime.now(timezone.utc).isoformat(),wallSeconds=time.monotonic()-ENTRY)
        publish_terminal(args.output/'terminal',state,m.publish_owner,primary_error)
    if primary_error is not None:raise primary_error
    assert state['status'].startswith('diagnostic-complete')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--authorization',type=Path,required=True);parser.add_argument('--pm-go',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--model',action='store_true')
    parser.add_argument('--teacher',action='store_true');parser.add_argument('--model-result',type=Path)
    parser.add_argument('--model-sha')
    parser.add_argument('--total-end',type=float);parser.add_argument('--work-end',type=float)
    main(parser.parse_args())
