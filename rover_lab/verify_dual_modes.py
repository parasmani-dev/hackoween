"""New UI local-mode HTTP verification in real Webots; provider networks blocked."""
import argparse,json,os,re,threading,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from rover_lab import dashboard
from agent.engine import EngineClient


def verify(engine_port,output,legacy_port):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    layout=output/'layout.txt';layout.write_text('A',encoding='utf-8')
    env={'ROVER_LIVE_ENABLED':'0','ANTHROPIC_API_KEY':'','OPENAI_API_KEY':'',
         'ROVER_DEMO_LAYOUT':str(layout),'ROVER_LAB_SCENARIO_ONE_ENGINE':f'http://127.0.0.1:{engine_port}'}
    with patch.dict(os.environ,env),patch.object(dashboard,'RUN_DIR',output), \
         patch.object(dashboard,'SCENARIO_ONE_ORIGIN',f'http://127.0.0.1:{legacy_port}'), \
         patch('agent.claude.request',side_effect=AssertionError('Provider network forbidden')), \
         patch('agent.openai_client.request',side_effect=AssertionError('Provider network forbidden')):
        server=dashboard.create_server(0,engine_port)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/scenario-1') as response:page=response.read().decode()
        assert 'value="local"' in page and 'value="live"' in page
        token=re.search(r'name="jury-token" content="([^"]+)"',page)[1]
        def call(path,data=None,auth=True):
            headers={'Origin':base,'Content-Type':'application/json'}
            if auth:headers['X-Jury-Token']=token
            with urlopen(Request(base+'/scenario-1/api'+path,data=None if data is None else json.dumps(data).encode(),headers=headers),timeout=40) as response:
                return json.load(response)
        def rejected(data,code,**kwargs):
            try:call('/live/start',data,**kwargs)
            except HTTPError as error:
                with error:assert error.code==code,error.code
            else:raise AssertionError('Unexpectedly accepted')
        results=[]
        try:
            call('/live/reset',{})
            rejected({'command':'drop','max_decisions':50,'execution_mode':'local'},403,auth=False)
            rejected({'command':'move forward 1 m then dance','max_decisions':50,'execution_mode':'local'},400)
            rejected({'command':'Fetch red sphere','max_decisions':50,'execution_mode':'live'},400)
            assert EngineClient(f'http://127.0.0.1:{engine_port}').state()['state']=='Idle'
            tests=[('bring the red ball to the green zone',50,'engine_done','pass'),
                   ('move forward 50 cm, turn left 90 degrees',50,'local_complete','completed'),
                   ('move forward 50 cm',1,'decision_limit','stopped'),
                   ('drop',50,'local_action_rejected','stopped')]
            for command,cap,expected,outcome in tests:
                call('/live/reset',{})
                call('/live/start',{'command':command,'max_decisions':cap,'execution_mode':'local'})
                deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    status=call('/live/status')
                    if not status['running']:break
                    time.sleep(.2)
                else:raise AssertionError('Local run timeout')
                assert status['execution_mode']=='local' and status['provider']=='local'
                assert status['result']['status']==expected,status
                assert status['result']['outcome']==outcome,status
                assert status['result']['provider_calls']==0,status
                assert status['result']['capture_status']=='complete',status
                attempt=server.live_run.attempt
                assert not list(attempt.glob('*requests*'))
                rows=[json.loads(x) for x in next(attempt.glob('run-*/steps.jsonl')).read_text().splitlines()]
                assert not any(row['result'].get('collided') for row in rows),rows
                assert len(rows)<=cap
                results.append({'command':command,'status':expected,'outcome':outcome,'steps':len(rows),'contacts':0})
            report={'provider_calls':0,'no_key_required':True,'ui_http':'pass','runs':results}
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
            call('/live/reset',{})
        finally:
            if server.live_run.thread:server.live_run.thread.join(40)
            server.shutdown();server.server_close();worker.join(3)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--engine-port',type=int,required=True)
    p.add_argument('--output',required=True);p.add_argument('--legacy-port',type=int,default=8812)
    a=p.parse_args();verify(a.engine_port,a.output,a.legacy_port)
