"""Analyze one actual GitHub attempt; no fabricated/estimated CI timing inputs."""
import argparse
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def read_text(path):
    raw=Path(path).read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')


def read_json(path):
    return json.loads(read_text(path))


def cases(suites):
    for suite in suites:
        yield from suite.get('specs',[])
        yield from cases(suite.get('suites',[]))


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-json',required=True)
parser.add_argument('--jobs-json',required=True)
parser.add_argument('--log',required=True)
parser.add_argument('--expected-head',required=True)
parser.add_argument('--expected-browser-json',required=True)
parser.add_argument('--report',action='append',required=True)
parser.add_argument('--shards',type=int,default=8)
parser.add_argument('--junit',action='append',default=[])
parser.add_argument('--python-collection',required=True)
parser.add_argument('--python-baseline-skips')
parser.add_argument('--python-coverage-root',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
run=read_json(args.run_json)
jobs=read_json(args.jobs_json)['jobs']
checks={'sameHead':run['head_sha']==args.expected_head,'workflowSucceeded':run['status']=='completed' and run['conclusion']=='success','completeBrowserReports':len(args.report)==args.shards}
expected_jobs={'python-checks (core 1/1)','python-checks (gateway 1/2)','python-checks (gateway 2/2)','contracts','contract-gateway-viewer',*(f'browser-acceptance ({n})' for n in range(1,args.shards+1))}
checks['completeJobs']={job['name'] for job in jobs}==expected_jobs and len(jobs)==len(expected_jobs)
job_rows=[]
for job in jobs:
    started=datetime.fromisoformat(job['started_at'].replace('Z','+00:00'))
    completed=datetime.fromisoformat(job['completed_at'].replace('Z','+00:00')) if job['completed_at'] else None
    seconds=(completed-started).total_seconds() if completed else None
    job_rows.append({'name':job['name'],'conclusion':job['conclusion'],'seconds':seconds,'url':job['html_url']})
checks['everyJobWithin200Seconds']=all(row['seconds'] is not None and row['seconds']<=200 for row in job_rows)
checks['everyJobSucceeded']=all(row['conclusion']=='success' for row in job_rows)
expected_browser=read_json(args.expected_browser_json)
expected_cases={(row['file'],row['title'],row.get('project','')) for row in expected_browser}
expected_skips={(row['file'],row['title'],row.get('project',''))
                for row in expected_browser if row.get('outcome')=='skipped'}
expected_python=set()
collection_text=read_text(args.python_collection)
if collection_text.lstrip().startswith('{'):
    collection=json.loads(collection_text)
    python_nodes=[row['nodeid'] for row in collection['items']] if 'items' in collection else collection['collected']
else:
    python_nodes=[line for line in collection_text.splitlines() if line.startswith('tests/') and '::' in line]
for nodeid in python_nodes:
    address,bracket,parameters=nodeid.partition('[')
    parts=address.split('::')
    expected_python.add(('.'.join([parts[0][:-3].replace('/','.'),*parts[1:-1]]),parts[-1]+bracket+parameters))
rows=[]
report_errors=[]
for path in args.report:
    report=read_json(path)
    report_errors.extend(report.get('errors',[]))
    for spec in cases(report['suites']):
        for test in spec['tests']:
            results=test['results']
            for result in results:
                rows.append({'file':Path(spec['file']).name,'title':spec['title'],'project':test['projectName'],'timeoutMs':test['timeout'],'durationMs':result['duration'],'status':result['status'],'retry':result['retry'],'report':str(Path(path).resolve()),'outcome':test['status'],'steps':result.get('steps',[])})
identities=[(row['file'],row['title'],row['project']) for row in rows]
checks['collectionPreserved']=set(identities)==expected_cases and len(identities)==len(expected_cases)
actual_skips={(row['file'],row['title'],row['project']) for row in rows if row['status']=='skipped'}
checks['skipSetPreserved']=actual_skips==expected_skips
checks['browserSucceededWithoutRetries']=not report_errors and all(row['status'] in ['passed','skipped'] and row['retry']==0 for row in rows)
approved_timeouts={(row['file'],row['title'],row.get('project','')):row['approvedTimeoutMs'] for row in expected_browser}
checks['effectiveTimeoutsPreserved']=all(row['timeoutMs']==approved_timeouts.get((row['file'],row['title'],row['project'])) for row in rows)
checks['everyCaseWithin60Percent']=all(row['timeoutMs']>0 and row['durationMs']*5<=row['timeoutMs']*3 for row in rows)
log=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',read_text(args.log))
python_rows=[]
for line in log.splitlines():
    match=re.match(r'^python-checks \((core|gateway) ([1-2])/([1-2])\)\t(?:Run Python suite with xdist|UNKNOWN STEP)\t.*?\b(\d+) passed(.*?) in',line)
    if match:
        skipped=re.search(r'(\d+) skipped',match[5])
        python_rows.append({'lane':match[1],'index':int(match[2]),'count':int(match[3]),'passed':int(match[4]),'skipped':int(skipped[1]) if skipped else 0})
checks['pythonCountsPreserved']=len(python_rows)==3 and {(row['lane'],row['index'],row['count']) for row in python_rows}=={('core',1,1),('gateway',1,2),('gateway',2,2)} and sum(row['passed']+row['skipped'] for row in python_rows)==len(expected_python)
validator_path=Path(__file__).resolve().parent.parents[3]/'.worktrees/issue-85-refresh/tools/check_ci_python_coverage.py'
spec=importlib.util.spec_from_file_location('ci_python_coverage',validator_path)
validator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
try:
    coverage=validator.validate(Path(args.python_coverage_root))
    checks['pythonWorkerSelectionAndExecutionCoverage']=coverage['completeNodes']==len(expected_python)
except (ValueError,KeyError,OSError,ET.ParseError) as error:
    coverage={'error':str(error)}
    checks['pythonWorkerSelectionAndExecutionCoverage']=False
python_identities=[]
python_skips=[]
if args.junit or args.python_collection:
    for path in args.junit:
        for case in ET.parse(path).iter('testcase'):
            identity=(case.get('classname'),case.get('name'))
            python_identities.append(identity)
            if case.find('skipped') is not None:
                python_skips.append(identity)
    checks['pythonCollectionPreserved']=len(args.junit)==3 and set(python_identities)==expected_python and len(python_identities)==len(expected_python)
checks['pythonSkipBaselineAvailable']=args.python_baseline_skips is not None
checks['pythonSkipSetPreserved']=False
if args.python_baseline_skips:
    expected_python_skips={tuple(row) for row in read_json(args.python_baseline_skips)}
    checks['pythonSkipSetPreserved']=set(python_skips)==expected_python_skips and len(python_skips)==len(expected_python_skips)
output={'runId':run['id'],'attempt':run['run_attempt'],'head':run['head_sha'],'url':run['html_url'],'valid':all(checks.values()),'checks':checks,'longestJobSeconds':max((row['seconds'] or 0 for row in job_rows),default=0),'python':python_rows,'pythonCoverage':coverage,'pythonIdentities':sorted(python_identities),'pythonSkippedIdentities':sorted(python_skips),'browser':{'passed':sum(row['status']=='passed' for row in rows),'skipped':len(actual_skips),'collected':len(expected_cases)},'jobs':job_rows,'cases':sorted(rows,key=lambda row:row['durationMs']/max(row['timeoutMs'],1),reverse=True),'sources':{'run':args.run_json,'jobs':args.jobs_json,'log':args.log,'reports':args.report,'expectedCollection':args.expected_browser_json,'junit':args.junit,'pythonCollection':args.python_collection,'pythonBaselineSkips':args.python_baseline_skips,'pythonCoverageRoot':args.python_coverage_root,'pythonCoverageValidator':str(validator_path)}}
Path(args.output).write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({key:output[key] for key in ['runId','attempt','head','valid','checks','longestJobSeconds','python','browser']}))
raise SystemExit(0 if output['valid'] else 1)
