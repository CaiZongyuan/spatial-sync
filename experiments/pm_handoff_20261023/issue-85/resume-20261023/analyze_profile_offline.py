"""Offline bounded interpretation of the single completed profile; no runtime calls."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'binary-window-profile-20261023T135506Z'

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def dump(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

marks = [json.loads(line) for line in (ROOT / 'node-marks-28844.ndjson').read_text().splitlines()]
by = {row['phase']: row for row in marks}
stamp = lambda name: int(by[name]['hrtimeNs']) / 1000
windows = {'reload-to-paired-dispatch': (stamp('reload-dispatch'), stamp('paired-metrics-dispatch')), 'paired-read': (stamp('paired-metrics-dispatch'), stamp('paired-metrics-resolved')), 'observer-close': (stamp('observer-close-dispatch'), stamp('observer-close-resolved')), 'primary-png': (stamp('primary-png-dispatch'), stamp('primary-png-resolved')), 'whole': (stamp('reload-dispatch'), stamp('primary-png-resolved'))}
trace = load(ROOT / 'chromium-reload-to-png.trace.json')
events = trace['traceEvents']
syncs = {row['args']['sync_id']: row for row in events if row.get('name') == 'clock_sync'}
control = load(ROOT / 'window-control.json')
sync_checks = []
for operation, key in [('start-and-sync', 'wire-reload-start'), ('end-sync', 'wire-png-end')]:
    row = next(item for item in control['profileControl'] if item['operation'] == operation)
    low, high = int(row['nodeSyncRequestStartNs']) / 1000, int(row['nodeSyncRequestEndNs']) / 1000
    event = syncs[key]
    assert low <= event['ts'] <= high
    sync_checks.append({'id': key, 'nodeRequestUs': [low, high], 'chromeTraceUs': event['ts'], 'enclosed': True, 'spanMs': (high-low)/1000})

node_results = []
for path in sorted((ROOT / 'node-cpu').glob('*.cpuprofile')):
    profile = load(path)
    pid = int(path.name.split('.')[3])
    clock = load(ROOT / f'node-clock-{pid}.json')
    node_marks = [json.loads(line) for line in (ROOT / f'node-marks-{pid}.ndjson').read_text().splitlines()]
    nodes = {n['id']: n for n in profile['nodes']}
    assert len(profile['samples']) == len(profile['timeDeltas']) and all(x >= 0 for x in profile['timeDeltas'])
    assert profile['startTime'] <= int(clock['hrtimeNs'])/1000 <= profile['endTime']
    assert all(profile['startTime'] <= int(row['hrtimeNs'])/1000 <= profile['endTime'] for row in node_marks)
    parent = {child: node['id'] for node in profile['nodes'] for child in node.get('children', [])}
    intervals = []
    now = profile['startTime']
    for node_id, delta in zip(profile['samples'], profile['timeDeltas']):
        assert node_id in nodes
        intervals.append((now, now+delta, node_id)); now += delta
    assert now <= profile['endTime'] + 1
    window_results = {}
    for label, (low, high) in windows.items():
        self_times = Counter(); flags = Counter(); total = idle = gc = 0
        for begin, end, node_id in intervals:
            duration = max(0, min(end, high)-max(begin, low))
            if not duration: continue
            total += duration
            frame = nodes[node_id]['callFrame']; name = frame.get('functionName') or '(anonymous)'
            self_times[(name, frame.get('url',''), frame.get('lineNumber'))] += duration
            if name == '(idle)': idle += duration
            if name == '(garbage collector)': gc += duration
            chain = []; current = node_id
            while current in nodes:
                f = nodes[current]['callFrame']; chain.append((f.get('functionName',''), f.get('url','')))
                if current not in parent: break
                current = parent[current]
            if any('checkMagic' in name for name, _ in chain): flags['wire-magic-inclusive'] += duration
            if any(any(token in url.lower() for token in ['reporter','expect','matchers','testinfo']) or '_addStep' in name for name,url in chain): flags['assertion-reporter-inclusive'] += duration
            if any('websocket' in name.lower() or 'websocket' in url.lower() for name,url in chain): flags['websocket-inclusive'] += duration
        window_results[label] = {'windowMs': (high-low)/1000, 'sampledMs': total/1000, 'idleMs': idle/1000, 'nonIdleMs': (total-idle)/1000, 'gcMs': gc/1000, 'inclusiveBucketsMsMayOverlap': {key:value/1000 for key,value in flags.items()}, 'topSelfMs': [{'function': key[0], 'url': key[1], 'line0':key[2], 'ms': value/1000} for key,value in self_times.most_common(12)]}
    node_results.append({'pid': pid, 'role': 'test-worker' if pid == 28844 else 'CLI-reporter', 'clock': clock, 'profileStartUs': profile['startTime'], 'profileEndUs':profile['endTime'], 'samples':len(profile['samples']), 'lastSampleUs':now, 'allClockMarksInsideProfile':True, 'windows':window_results})
dump('node-window-cpu.json', node_results)

threads = {(row['pid'],row['tid']):row['args']['name'] for row in events if row.get('ph')=='M' and row.get('name')=='thread_name'}
processes = {row['pid']:row['args']['name'] for row in events if row.get('ph')=='M' and row.get('name')=='process_name'}
tasks = defaultdict(list)
for event in events:
    if event.get('ph')=='X' and event.get('name') in ['ThreadControllerImpl::RunTask','ThreadPool_RunTask']:
        tasks[(event['pid'],event['tid'])].append(event)

def union(intervals):
    total = 0; last_low = last_high = None
    for low,high in sorted(intervals):
        if last_low is None: last_low,last_high = low,high
        elif low <= last_high: last_high=max(last_high,high)
        else: total += last_high-last_low; last_low,last_high=low,high
    return total + (last_high-last_low if last_low is not None else 0)

chrome_results = []
for key, rows in tasks.items():
    selected = [row for row in rows if row['name']=='ThreadControllerImpl::RunTask'] or rows
    output = {}
    for label,(low,high) in windows.items():
        relevant=[row for row in selected if row['ts']<high and row['ts']+row['dur']>low]
        spans=[(max(row['ts'],low),min(row['ts']+row['dur'],high)) for row in relevant]
        outer=[]; outer_end=-1
        for row in sorted(relevant,key=lambda row:(row['ts'],-row['dur'])):
            if row['ts']+row['dur']<=outer_end: continue
            outer.append(row); outer_end=row['ts']+row['dur']
        cpu=[row for row in outer if 'tdur' in row and row['ts']>=low and row['ts']+row['dur']<=high]
        output[label]={'taskUnionWallMs':union(spans)/1000,'fullyContainedOuterTaskReportedCpuMs':sum(row['tdur'] for row in cpu)/1000,'tasks':len(relevant),'outerTasksWithCpu':len(cpu),'boundaryOrMissingCpuTasks':len(outer)-len(cpu)}
    if output['whole']['taskUnionWallMs']:
        chrome_results.append({'pid':key[0],'tid':key[1],'process':processes.get(key[0],''),'thread':threads.get(key,''),'windows':output})
chrome_results.sort(key=lambda row:row['windows']['whole']['taskUnionWallMs'],reverse=True)
dump('chromium-window-tasks.json', chrome_results)

source = (ROOT/'original-subject.ts').read_bytes()
assert hashlib.sha256(source).hexdigest()=='1f6cc2c194a6ef53f89fb79c7e1d4bbf8590ad414d552164c22ba4d6fac46566'
receipt = load(ROOT/'receipt.json')
assert hashlib.sha256((ROOT/'profile-subject.ts').read_bytes()).hexdigest()==receipt['profileSubjectSha256']
traffic=[]
for first,last in [('reload-dispatch','paired-metrics-dispatch'),('paired-metrics-dispatch','paired-metrics-resolved'),('observer-close-dispatch','observer-close-resolved'),('primary-png-dispatch','primary-png-resolved'),('reload-dispatch','primary-png-resolved')]:
    a,b=by[first],by[last]
    for side,field in [('primary','binaryFrames'),('observer','observerBinaryFrames')]:
        assert a['magicChecks'][side]==a[field] and b['magicChecks'][side]==b[field]
        traffic.append({'phase':first+'→'+last,'side':side,'callbacks':b[field]-a[field],'bytes':b['profileBytes'][side]-a['profileBytes'][side]})
overhead={p.name:load(p) for p in ROOT.glob('mark-overhead-*.json')}
alignment={'nodeHrtimeAndCpuProfileAnchorsValidated':True,'chromiumClockDomain':trace['metadata'].get('clock-domain'),'syncChecks':sync_checks,'windowMs':{key:(b-a)/1000 for key,(a,b) in windows.items()},'nodeMarkWriteTotalMs':{name:sum(row['measuredWriteNs'])/1e6 for name,row in overhead.items()},'collectorControl':control['profileControl'],'traffic':traffic,'traceEvents':len(events),'traceProcessorStats':trace['metadata'].get('trace_processor_stats'),'explicitTracingCompleteDataLossFlagRetained':False,'limits':['V8 sampling attribution is approximate at5ms; inclusive category buckets overlap.','Chrome task wall union is not CPU utilization; reported thread CPU only counts fully contained outer tasks with tdur.','No physical-GPU/CUDA utilization is inferred from GPU-process trace tasks.','Explicit tracingComplete dataLossOccurred was not retained; stream EOF, valid JSON and both clock syncs are verified, but zero trace loss is not asserted.','Windows current-patch profile does not reproduce or explain by itself prior Linux6.720s API interval.']}
dump('clock-overhead-traffic.json',alignment)
print(json.dumps({'windowMs':alignment['windowMs'],'markerWriteMs':alignment['nodeMarkWriteTotalMs'],'nodeWhole':[{'role':row['role'],**{key:row['windows']['whole'][key] for key in ['sampledMs','idleMs','nonIdleMs','gcMs','inclusiveBucketsMsMayOverlap']}} for row in node_results],'chromeMain':[row for row in chrome_results if row['thread'] in ['CrRendererMain','CrBrowserMain','CrGpuMain','VizCompositorThread']],'traceProcessorStats':alignment['traceProcessorStats']},ensure_ascii=True,indent=2))
